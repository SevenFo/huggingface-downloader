import os
import sys
import argparse
import fnmatch
import time
import signal
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed

# 从同级模块导入
from .utils import (
    print_color,
    check_command,
    ensure_ownership,
    run_command,
    signal_handler,
    is_inside_git_repo,
    RED,
    GREEN,
    YELLOW,
    BLUE,
)
from .core import (
    download_file,
    check_file_hash,
    get_download_url,
    get_lfs_files,
    create_lfs_placeholders,
)


def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="从 Hugging Face 或 ModelScope 使用提供的 repo ID 下载模型或数据集。"
    )
    parser.add_argument(
        "repo_id",
        help="Hugging Face 或 ModelScope repo ID，格式通常为 'org/repo_name'。",
    )
    parser.add_argument(
        "--source",
        choices=["huggingface", "modelscope"],
        default="huggingface",
        help="选择下载源平台。默认为 huggingface。",
    )
    parser.add_argument(
        "--include", nargs="+", default=[], help="要包含下载的文件模式。"
    )
    parser.add_argument(
        "--exclude", nargs="+", default=[], help="要排除下载的文件模式。"
    )
    parser.add_argument(
        "--hf_username", help="Hugging Face 或 ModelScope 用户名，用于认证。"
    )
    parser.add_argument(
        "--hf_token", help="Hugging Face 或 ModelScope 令牌，用于认证。"
    )
    parser.add_argument(
        "--endpoint",
        help="覆盖默认的源平台端点 (例如 https://huggingface.co 或 https://modelscope.cn)。",
    )
    parser.add_argument(
        "--tool",
        choices=["aria2c", "wget"],
        default="aria2c",
        help="选择下载工具。默认是 aria2c。",
    )
    parser.add_argument(
        "-x",
        "--threads",  # 添加长选项名
        type=int,
        default=4,
        dest="threads",  # 确保目标变量名一致
        help="aria2c 的下载线程数。默认是 4。",
    )
    parser.add_argument("--dataset", action="store_true", help="标志，表示下载数据集。")
    parser.add_argument("--local_dir", help="本地存储模型或数据集的目录路径。")
    parser.add_argument(
        "--max_retries", type=int, default=10, help="最大重试次数，默认为 10。"
    )
    parser.add_argument("--verify_hash", action="store_true", help="启用哈希验证。")
    parser.add_argument(
        "--remove_git",
        action="store_true",
        default=False,
        help="下载完成后移除.git目录。",
    )
    parser.add_argument(
        "--full_clone",
        action="store_true",
        default=False,
        help="执行完整克隆（下载所有历史记录），而不是默认的浅克隆 (--depth=1)。",
    )
    # 添加并行下载和校验的工作线程数控制
    parser.add_argument(
        "--download_workers",
        type=int,
        default=8,  # 可以调整默认值
        help="用于并行下载的最大工作线程数。",
    )
    parser.add_argument(
        "--verify_workers",
        type=int,
        default=4,  # 可以调整默认值
        help="用于并行哈希校验的最大工作线程数。",
    )

    return parser.parse_args()


def main():
    """主执行逻辑。"""
    args = parse_args()
    original_dir = os.getcwd()  # 记录原始目录

    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 检查依赖命令
    check_command("git")
    check_command("git-lfs")
    check_command(args.tool)

    # --- 端点和仓库 ID 处理 ---
    default_endpoints = {
        "huggingface": os.environ.get("HF_ENDPOINT", "https://huggingface.co"),
        "modelscope": os.environ.get("MODELSCOPE_ENDPOINT", "https://modelscope.cn"),
    }
    base_endpoint = default_endpoints.get(args.source)
    if not base_endpoint:
        print_color(f"错误：不支持的源 '{args.source}'", RED)
        sys.exit(1)
    endpoint = args.endpoint or base_endpoint
    print_color(f"使用端点: {endpoint} (来源: {args.source})", BLUE)

    raw_repo_id = args.repo_id
    repo_id_for_clone = raw_repo_id
    repo_id_for_url = raw_repo_id

    if args.source == "huggingface":
        # HF clone URL 不需要前缀，下载 URL 可能需要 dataset 前缀
        repo_id_for_url = f"datasets/{raw_repo_id}" if args.dataset else raw_repo_id
        repo_id_for_clone = raw_repo_id  # Clone URL 就是 'org/repo'
    elif args.source == "modelscope":
        # MS clone URL 需要前缀，下载 URL 不需要
        repo_id_for_clone = (
            f"datasets/{raw_repo_id}" if args.dataset else f"models/{raw_repo_id}"
        )
        repo_id_for_url = raw_repo_id  # API URL 不需要前缀
    print_color(f"仓库 ID (克隆): {repo_id_for_clone}", BLUE)
    print_color(f"仓库 ID (下载): {repo_id_for_url}", BLUE)

    # --- 本地目录处理 ---
    repo_name = raw_repo_id.split("/")[-1]
    target_local_dir = args.local_dir or repo_name

    # 处理在 Git 仓库内运行的情况
    if is_inside_git_repo(original_dir) and not args.local_dir:
        print_color("检测到在Git仓库内运行脚本且未指定 local_dir", YELLOW)
        temp_suffix = int(time.time())
        target_local_dir = f"{repo_name}_{temp_suffix}"
        print_color(f"使用临时目录名: {target_local_dir}", YELLOW)

    # 计算绝对路径
    model_dir = os.path.abspath(os.path.join(original_dir, target_local_dir))
    print_color(f"目标本地目录: {model_dir}", BLUE)

    try:
        # --- Git 克隆或拉取 ---
        git_dir_path = os.path.join(model_dir, ".git")
        is_existing_repo = os.path.isdir(git_dir_path)

        if is_existing_repo:
            print_color(f"仓库目录 {model_dir} 已存在，尝试更新...", YELLOW)
            ensure_ownership(model_dir)  # 检查所有权
            try:
                os.chdir(model_dir)  # 进入仓库目录执行 git pull
                # 确保当前仓库的 LFS 钩子已安装
                run_command("git lfs install --local")
                run_command("GIT_LFS_SKIP_SMUDGE=1 git pull")
                print_color("仓库更新完成。", GREEN)
            except Exception as e:
                print_color(f"更新仓库失败: {e}", RED)
                # 可以选择退出或继续尝试下载 LFS 文件
        else:
            print_color(
                f"仓库目录 {model_dir} 不存在或不是 Git 仓库，开始克隆...", BLUE
            )
            # 如果目录存在但不是 Git 仓库，警告
            if os.path.exists(model_dir) and not is_existing_repo:
                print_color(f"警告: 目录 {model_dir} 已存在但不是 Git 仓库。", YELLOW)
                user_input = input("是否清空并重新克隆? [y/N]: ").lower()
                if user_input == "y":
                    try:
                        shutil.rmtree(model_dir)
                        print_color(f"已清空目录 {model_dir}", YELLOW)
                    except OSError as e:
                        print_color(f"清空目录失败: {e}", RED)
                        return  # 无法继续
                else:
                    print_color("操作取消。", RED)
                    return

            # 构建克隆 URL (带认证)
            repo_url = f"{endpoint}/{repo_id_for_clone}"
            if args.hf_username and args.hf_token:
                endpoint_no_proto = endpoint.replace("https://", "").replace(
                    "http://", ""
                )
                repo_url = f"https://{args.hf_username}:{args.hf_token}@{endpoint_no_proto}/{repo_id_for_clone}"

            # 构建克隆命令 - 移除 pre-install 部分
            clone_depth = "--depth=1" if not args.full_clone else ""
            if not args.full_clone:
                print_color(
                    "默认使用浅克隆 (--depth=1)。使用 --full_clone 进行完整克隆。",
                    YELLOW,
                )
            else:
                print_color("进行完整克隆 (--full_clone)。", YELLOW)

            # 只包含 clone 命令
            clone_command = f'GIT_LFS_SKIP_SMUDGE=1 git clone {clone_depth} {repo_url} "{model_dir}"'

            try:
                run_command(clone_command, cwd=original_dir)  # 在原始目录执行克隆
                print_color("克隆完成。", GREEN)
                os.chdir(model_dir)  # 进入新克隆的仓库
                ensure_ownership(model_dir)  # 克隆后检查所有权
                # 在新克隆的仓库中安装本地 LFS 钩子
                print_color("在新克隆的仓库中安装本地 Git LFS 钩子...", BLUE)
                run_command("git lfs install --local")
            except Exception as e:
                print_color(f"克隆仓库或安装 LFS 钩子失败: {e}", RED)  # 更新错误消息
                # 检查是否是认证失败的常见情况
                if "Authentication failed" in str(
                    e
                ) or "could not read Username" in str(e):
                    print_color(
                        "认证失败。请检查您的用户名和令牌/密码，或仓库是否需要认证。",
                        YELLOW,
                    )
                return  # 克隆失败则无法继续

        # --- LFS 文件处理 ---
        # 切换到模型目录进行后续操作
        if os.getcwd() != model_dir:
            os.chdir(model_dir)

        lfs_files_info = get_lfs_files(model_dir)
        if lfs_files_info is None:
            print_color("无法获取 LFS 文件列表，可能仓库为空或出现错误。", RED)
            return  # 无法继续

        if not lfs_files_info:
            print_color("仓库中没有找到 LFS 文件。", GREEN)
            # 如果没有 LFS 文件，根据 remove_git 选项处理 .git 目录
            if args.remove_git:
                git_dir = os.path.join(model_dir, ".git")
                if os.path.isdir(git_dir):
                    print_color(
                        f"根据 --remove_git 选项，移除 {git_dir} 目录。", YELLOW
                    )
                    try:
                        shutil.rmtree(git_dir)
                        print_color(f"已移除 {git_dir} 目录。", GREEN)
                    except Exception as e:
                        print_color(f"移除 {git_dir} 目录失败: {e}", RED)
                else:
                    print_color(f"默认保留 .git 目录。", YELLOW)
            print_color("所有操作完成。", GREEN)
            return  # 没有 LFS 文件，任务完成

        # 创建占位文件（如果需要）
        create_lfs_placeholders(lfs_files_info, model_dir)

        # --- 文件过滤和分类 ---
        files_to_download_tasks = []
        files_to_verify_tasks = []
        files_to_verify_after_download_map = {}  # path -> hash

        def matches_patterns(file_path, patterns):
            return any(fnmatch.fnmatch(file_path, pattern) for pattern in patterns)

        for file_info in lfs_files_info:
            file_path = file_info["path"]
            abs_path = file_info["abs_path"]  # 使用绝对路径
            lfs_hash = file_info["hash"]
            status = file_info["status"]
            is_pointer = status == "-"  # LFS 指针文件，未下载

            # 应用 include/exclude 规则
            if args.include and not matches_patterns(file_path, args.include):
                # print_color(f"跳过 {file_path} (不匹配包含模式)", YELLOW)
                continue
            if args.exclude and matches_patterns(file_path, args.exclude):
                print_color(f"跳过 {file_path} (匹配排除模式)", YELLOW)
                continue

            # 生成下载 URL
            url = get_download_url(
                endpoint, repo_id_for_url, file_path, args.source, args.dataset
            )

            # 分类任务
            if args.verify_hash:
                if not is_pointer:  # 文件已存在 (可能是完整文件或旧指针)
                    # print_color(f"文件 {file_path} 已存在，添加到校验队列。", YELLOW)
                    files_to_verify_tasks.append((abs_path, lfs_hash))
                else:  # 文件是 LFS 指针，需要下载
                    # print_color(f"文件 {file_path} 未下载，添加到下载队列，并将在下载后校验。", YELLOW)
                    files_to_download_tasks.append(
                        (
                            url,
                            abs_path,
                            args.tool,
                            args.threads,
                            args.hf_token,
                            args.max_retries,
                        )
                    )
                    files_to_verify_after_download_map[abs_path] = lfs_hash
            elif is_pointer:  # 未开启校验，只下载 LFS 指针文件
                # print_color(f"文件 {file_path} 未下载，添加到下载队列。", YELLOW)
                files_to_download_tasks.append(
                    (
                        url,
                        abs_path,
                        args.tool,
                        args.threads,
                        args.hf_token,
                        args.max_retries,
                    )
                )
            # else: # 未开启校验且文件已存在，跳过
            # print_color(f"文件 {file_path} 已存在且未开启哈希验证，跳过。", GREEN)

        # --- 执行校验 (已存在文件) ---
        if files_to_verify_tasks:
            print_color(
                f"开始校验 {len(files_to_verify_tasks)} 个已存在文件的哈希 (使用 {args.verify_workers} 个工作线程)...",
                BLUE,
            )
            with ProcessPoolExecutor(max_workers=args.verify_workers) as executor:
                futures = {
                    executor.submit(check_file_hash, task): task
                    for task in files_to_verify_tasks
                }
                for future in as_completed(futures):
                    original_task = futures[future]
                    file_path, expected_hash = original_task
                    try:
                        _, is_valid = future.result()
                        if is_valid:
                            # print_color(f"文件 {os.path.basename(file_path)} 校验通过。", GREEN)
                            pass  # 校验通过，无需操作
                        else:
                            print_color(
                                f"文件 {os.path.basename(file_path)} 校验失败，添加到下载队列。",
                                YELLOW,
                            )
                            # 重新生成 URL 并添加到下载任务
                            relative_path = os.path.relpath(file_path, model_dir)
                            url = get_download_url(
                                endpoint,
                                repo_id_for_url,
                                relative_path,
                                args.source,
                                args.dataset,
                            )
                            files_to_download_tasks.append(
                                (
                                    url,
                                    file_path,
                                    args.tool,
                                    args.threads,
                                    args.hf_token,
                                    args.max_retries,
                                )
                            )
                            # 标记下载后需要校验
                            files_to_verify_after_download_map[file_path] = (
                                expected_hash
                            )
                    except Exception as e:
                        print_color(
                            f"校验文件 {os.path.basename(file_path)} 时出错: {e}", RED
                        )
                        # 也可以选择将校验失败的文件加入下载列表

        # --- 执行下载 ---
        total_files_to_download = len(files_to_download_tasks)
        if total_files_to_download > 0:
            print_color(
                f"开始下载 {total_files_to_download} 个文件 (使用 {args.download_workers} 个工作线程)...",
                BLUE,
            )
            start_time_download = time.time()
            completed_downloads = 0
            download_successful_files = set()  # 使用集合跟踪成功下载的文件绝对路径

            with ProcessPoolExecutor(max_workers=args.download_workers) as executor:
                # 修正：将 download_file 作为第一个参数传递给 submit
                futures = {
                    executor.submit(download_file, *task): task
                    for task in files_to_download_tasks
                }
                for future in as_completed(futures):
                    original_task = futures[future]
                    # original_task 是 (url, file_path, tool, threads, token, max_retries)
                    url, file_path, *_ = original_task  # 获取文件路径用于日志记录
                    try:
                        elapsed_time = (
                            future.result()
                        )  # download_file 成功返回时间，失败返回 None
                        if elapsed_time is not None:
                            completed_downloads += 1
                            download_successful_files.add(file_path)  # 添加绝对路径
                            # 打印进度
                            total_elapsed = time.time() - start_time_download
                            avg_time = (
                                total_elapsed / completed_downloads
                                if completed_downloads > 0
                                else 0
                            )
                            remaining = total_files_to_download - completed_downloads
                            eta = avg_time * remaining
                            print_color(
                                f"下载进度: {completed_downloads}/{total_files_to_download} | "
                                f"用时: {total_elapsed:.2f}s | ETA: {eta:.2f}s",
                                BLUE,
                            )
                        else:
                            # download_file 内部已打印失败信息
                            pass
                    except Exception as e:
                        print_color(
                            f"下载文件 {os.path.basename(file_path)} 时发生意外错误: {e}",
                            RED,
                        )
        else:
            print_color("没有需要下载的文件。", GREEN)

        # --- 执行校验 (新下载文件) ---
        files_to_verify_now = [
            (path, lfs_hash)
            for path, lfs_hash in files_to_verify_after_download_map.items()
            if path in download_successful_files  # 只校验本次成功下载的文件
        ]

        if files_to_verify_now:
            print_color(
                f"开始校验 {len(files_to_verify_now)} 个新下载文件的哈希 (使用 {args.verify_workers} 个工作线程)...",
                BLUE,
            )
            verification_failed_count = 0
            with ProcessPoolExecutor(max_workers=args.verify_workers) as executor:
                futures = {
                    executor.submit(check_file_hash, task): task
                    for task in files_to_verify_now
                }
                for future in as_completed(futures):
                    original_task = futures[future]
                    file_path, _ = original_task
                    try:
                        _, is_valid = future.result()
                        if is_valid:
                            # print_color(f"新文件 {os.path.basename(file_path)} 校验通过。", GREEN)
                            pass
                        else:
                            verification_failed_count += 1
                            print_color(
                                f"新文件 {os.path.basename(file_path)} 校验失败！", RED
                            )
                    except Exception as e:
                        verification_failed_count += 1
                        print_color(
                            f"校验新文件 {os.path.basename(file_path)} 时出错: {e}", RED
                        )
            if verification_failed_count > 0:
                print_color(
                    f"{verification_failed_count} 个新下载的文件校验失败。请检查或尝试重新运行。",
                    RED,
                )
            else:
                print_color("所有新下载的文件校验通过。", GREEN)

        # --- 清理 .git 目录 ---
        if args.remove_git:
            git_dir = os.path.join(model_dir, ".git")
            if os.path.isdir(git_dir):
                print_color(
                    f"根据 --remove_git 选项，准备移除 {git_dir} 目录。", YELLOW
                )
                user_input = input("确认移除 .git 目录? [y/N]: ").lower()
                if user_input == "y":
                    try:
                        shutil.rmtree(git_dir)
                        print_color(f"已移除 {git_dir} 目录。", GREEN)
                    except Exception as e:
                        print_color(f"移除 {git_dir} 目录失败: {e}", RED)
                else:
                    print_color("取消移除，保留 .git 目录。", YELLOW)
            # else: # 如果 .git 不存在，则无需提示
            #     print_color(f"未找到 {git_dir} 目录，无需移除。", YELLOW)
        else:
            print_color("默认保留 .git 目录。使用 --remove_git 进行移除。", YELLOW)

        print_color("所有操作完成。", GREEN)

    except Exception as e:
        print_color(f"发生未处理的错误: {e}", RED)
        import traceback

        traceback.print_exc()  # 打印详细错误堆栈
    finally:
        # 确保恢复原始工作目录
        if os.getcwd() != original_dir:
            os.chdir(original_dir)
        print_color(f"已返回目录: {os.getcwd()}", BLUE)


def run():
    """命令行入口点函数。"""
    try:
        main()
    except KeyboardInterrupt:
        print_color("\n操作被用户中断。", YELLOW)
        sys.exit(1)
    except Exception as e:
        # main 函数内部应该已经处理并打印了错误
        # 这里可以再加一层保障
        print_color(f"脚本执行时发生致命错误: {e}", RED)
        sys.exit(1)


if __name__ == "__main__":
    # 如果直接运行此文件（虽然不推荐，入口点是 run），也执行 main
    run()
