#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import shutil
import fnmatch
import time
import signal
import hashlib
from concurrent.futures import ProcessPoolExecutor, as_completed
import urllib.parse

# 终端输出颜色定义
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
NC = "\033[0m"  # 无颜色
BLUE = "\033[0;34m"

# 全局列表来跟踪子进程
subprocesses = []


def print_color(message, color=NC):
    print(f"{color}{message}{NC}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="从 Hugging Face 或 ModelScope 使用提供的 repo ID 下载模型或数据集。"  # Updated description
    )
    parser.add_argument(
        "repo_id",
        help="Hugging Face 或 ModelScope repo ID，格式通常为 'org/repo_name'。",  # Updated help
    )
    parser.add_argument(
        "--source",
        choices=["huggingface", "modelscope"],
        default="huggingface",
        help="选择下载源平台。默认为 huggingface。",
    )
    parser.add_argument("--include", nargs="+", help="要包含下载的文件模式。")
    parser.add_argument(
        "--exclude",
        nargs="+",
        help="要排除下载的文件模式。",
    )
    parser.add_argument(
        "--hf_username", help="Hugging Face 或 ModelScope 用户名，用于认证。"
    )  # Updated help
    parser.add_argument(
        "--hf_token", help="Hugging Face 或 ModelScope 令牌，用于认证。"
    )  # Updated help
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
        type=int,
        default=4,
        help="aria2c 的下载线程数。默认是 4。",
    )
    parser.add_argument("--dataset", action="store_true", help="标志，表示下载数据集。")
    parser.add_argument(
        "--local_dir",
        help="本地存储模型或数据集的目录路径。",
    )
    parser.add_argument(
        "--max_retries",
        type=int,
        default=10,
        help="最大重试次数，默认为 10。",
    )
    parser.add_argument("--verify_hash", action="store_true", help="启用哈希验证。")
    parser.add_argument(
        "--remove_git",
        action="store_true",
        help="下载完成后移除.git目录，避免Git仓库嵌套问题。",
        default=True,
    )
    parser.add_argument(
        "--depth-1",
        action="store_true",
        help="使用 --depth=1 进行浅克隆，只下载最新提交，减小下载体积。默认启用。",
        default=True,
    )
    return parser.parse_args()


def check_command(command):
    if shutil.which(command) is None:
        print_color(f"{command} 未安装，请先安装。", RED)
        sys.exit(1)


def ensure_ownership(repo_dir):
    # 确保目录存在
    if not os.path.isdir(repo_dir):
        print_color(f"目录 {repo_dir} 不存在，无法确保所有权", RED)
        return

    # 检查是否为有效的 Git 仓库目录
    git_dir_path = os.path.join(repo_dir, ".git")
    if not os.path.isdir(git_dir_path):
        print_color(f"{repo_dir} 不是一个有效的 Git 仓库目录，跳过所有权检查。", YELLOW)
        return

    try:
        subprocess.check_output(
            ["git", "status"], cwd=repo_dir, stderr=subprocess.STDOUT
        )
    except subprocess.CalledProcessError as e:
        if hasattr(e, "output") and "detected dubious ownership" in e.output.decode():
            subprocess.run(
                ["git", "config", "--global", "--add", "safe.directory", repo_dir]
            )
            print_color(f"已将 {repo_dir} 标记为 git 安全目录。", YELLOW)
        else:
            print_color(f"在 {repo_dir} 运行 git status 时出错: {e}", RED)


def run_command(command):
    print_color(f"运行命令: {command}", BLUE)
    process = subprocess.Popen(command, shell=True)
    subprocesses.append(process)
    process.wait()
    if process.returncode != 0:
        raise Exception(f"命令失败: {command}")


def is_file_downloaded(file_path, expected_hash):
    """校验文件是否已经通过哈希值下载"""
    if not os.path.isfile(file_path):
        return False

    # 计算整个文件的 SHA-256 哈希值
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):  # 逐块读取文件
            sha256.update(chunk)
    file_hash = sha256.hexdigest()[:10]  # 取前 10 个字符

    return file_hash == expected_hash


def download_file(url, file_path, tool, threads, token=None, max_retries=10):
    # 确保文件路径非空
    if not file_path:
        print_color(f"错误: 收到空文件路径", RED)
        return 0

    # 使用绝对路径构建目标目录和文件
    dir_path = os.path.dirname(os.path.abspath(file_path))
    filename = os.path.basename(file_path)

    # 确保目标目录存在
    os.makedirs(dir_path, exist_ok=True)

    if tool == "wget":
        command = f'wget -c "{url}" -O "{file_path}"'
        if token:
            command = f'wget --header="Authorization: Bearer {token}" -c "{url}" -O "{file_path}"'
    else:  # aria2c
        command = f'aria2c --console-log-level=error --file-allocation=none -x {threads} -s {threads} -k 1M -c "{url}" -d "{dir_path}" -o "{filename}"'
        if token:
            command = f'aria2c --header="Authorization: Bearer {token}" --console-log-level=error --file-allocation=none -x {threads} -s {threads} -k 1M -c "{url}" -d "{dir_path}" -o "{filename}"'

    for attempt in range(1, max_retries + 1):
        try:
            print_color(f"开始下载 {file_path} (第 {attempt} 次尝试)", YELLOW)
            start_time = time.time()
            run_command(command)
            elapsed_time = time.time() - start_time
            print_color(f"成功下载 {url}，用时 {elapsed_time:.2f} 秒。", GREEN)
            return elapsed_time
        except Exception as e:
            print_color(f"休眠 {1.5**attempt} 秒", YELLOW)
            time.sleep(2**attempt)
            print_color(f"第 {attempt} 次尝试失败: {e}", RED)
            if attempt == max_retries:
                print_color(f"在 {max_retries} 次尝试后未能下载 {url}。", RED)
                raise


def signal_handler(sig, frame):
    print_color("接收到终止信号。停止所有子进程...", RED)
    for process in subprocesses:
        print_color(f"终止进程 {process.pid}", RED)
        try:
            process.kill()  # 使用 kill 强制终止进程
        except Exception as e:
            print_color(f"终止进程 {process.pid} 失败: {e}", RED)
    sys.exit(0)


def check_file_hash(file_info):
    file_path, expected_hash = file_info
    return (file_path, is_file_downloaded(file_path, expected_hash))


def get_download_url(endpoint, repo_id, file_path, source, is_dataset):
    """根据源平台生成下载 URL"""
    encoded_file_path = urllib.parse.quote(file_path, safe="")  # URL encode file path
    if source == "huggingface":
        return f"{endpoint}/{repo_id}/resolve/main/{encoded_file_path}"
    elif source == "modelscope":
        # ModelScope API 路径不同
        api_base = "models" if not is_dataset else "datasets"
        # ModelScope 可能不需要 repo_id 中的 'modelscope/' 或 'datasets/' 前缀
        # 假设 repo_id 已经是 'org/repo_name' 格式
        # ModelScope URL 格式: https://modelscope.cn/api/v1/{api_base}/{repo_id}/repo?Revision=master&FilePath={file_path}
        return f"{endpoint}/api/v1/{api_base}/{repo_id}/repo?Revision=master&FilePath={encoded_file_path}"
    else:
        raise ValueError(f"不支持的源: {source}")


def main():
    signal.signal(signal.SIGINT, signal_handler)
    args = parse_args()

    check_command("git")
    check_command("git-lfs")
    check_command(args.tool)

    # 根据源设置默认端点
    default_endpoints = {
        "huggingface": os.environ.get("HF_ENDPOINT", "https://huggingface.co"),
        "modelscope": os.environ.get("MODELSCOPE_ENDPOINT", "https://modelscope.cn"),
    }

    base_endpoint = default_endpoints.get(args.source)
    if not base_endpoint:
        print_color(f"错误：不支持的源 '{args.source}'", RED)
        sys.exit(1)

    # 使用用户提供的端点或基于源的默认端点
    endpoint = args.endpoint or base_endpoint
    print_color(f"使用端点: {endpoint} (来源: {args.source})", BLUE)

    # 根据源和类型调整 repo_id
    raw_repo_id = args.repo_id  # 原始用户输入 'org/repo_name'
    repo_id_for_clone = raw_repo_id  # 在这里初始化 repo_id_for_clone
    if args.source == "huggingface":
        repo_id_for_url = f"datasets/{raw_repo_id}" if args.dataset else raw_repo_id
    elif args.source == "modelscope":
        # ModelScope API URL 通常只需要 'org/repo_name'
        # 但 git clone 可能需要前缀，这里我们为 clone URL 添加前缀
        repo_id_for_clone = (
            f"datasets/{raw_repo_id}" if args.dataset else f"modelscope/{raw_repo_id}"
        )
        repo_id_for_url = raw_repo_id  # API URL 不需要前缀
    else:
        # Fallback or error
        repo_id_for_url = raw_repo_id
        repo_id_for_clone = raw_repo_id

    local_dir = args.local_dir or raw_repo_id.split("/")[-1]

    # 获取当前工作目录以便后续恢复
    original_dir = os.getcwd()

    # 检查当前目录是否已经是一个Git仓库
    in_git_repo = False
    try:
        subprocess.check_output(
            ["git", "rev-parse", "--is-inside-work-tree"], stderr=subprocess.STDOUT
        )
        in_git_repo = True
        print_color("检测到在Git仓库内运行脚本", YELLOW)
    except subprocess.CalledProcessError:
        pass

    # 如果在Git仓库中运行且用户未指定本地目录，则使用临时目录名避免冲突
    if in_git_repo and not args.local_dir:
        temp_suffix = int(time.time())
        local_dir = f"{local_dir}_{temp_suffix}"
        print_color(f"在Git仓库中运行，使用临时目录名: {local_dir}", YELLOW)

    try:
        # 记录模型目录的绝对路径，避免路径问题
        model_dir = os.path.abspath(os.path.join(original_dir, local_dir))

        if os.path.isdir(os.path.join(model_dir, ".git")):
            print_color(f"{model_dir} 已存在，跳过克隆。", YELLOW)
            os.chdir(model_dir)
            ensure_ownership(model_dir)
            run_command("GIT_LFS_SKIP_SMUDGE=1 git pull")
        else:
            # 使用 repo_id_for_clone 进行克隆
            repo_url = f"{endpoint}/{repo_id_for_clone}"
            if args.hf_username and args.hf_token:
                # 移除 https:// 前缀以插入凭据
                endpoint_no_proto = endpoint.replace("https://", "").replace(
                    "http://", ""
                )
                repo_url = f"https://{args.hf_username}:{args.hf_token}@{endpoint_no_proto}/{repo_id_for_clone}"

            # 如果目录已存在但不是Git仓库，提醒用户
            if os.path.exists(model_dir):
                print_color(
                    f"警告: 目录 {model_dir} 已存在但不是Git仓库。克隆可能失败。",
                    YELLOW,
                )
                user_input = input("是否继续? [y/N]: ").lower()
                if user_input != "y":
                    print_color("操作已取消。", RED)
                    return

            # 显示浅克隆提示
            if args.depth_1:
                print_color(
                    "使用 --depth=1 进行浅克隆，只下载最新提交，减小下载体积。", YELLOW
                )
                clone_command = (
                    f"GIT_LFS_SKIP_SMUDGE=1 git clone --depth=1 {repo_url} {local_dir}"
                )
            else:
                print_color(
                    "进行完整克隆，下载所有历史记录。这可能需要更长的时间。", YELLOW
                )
                clone_command = (
                    f"GIT_LFS_SKIP_SMUDGE=1 git clone {repo_url} {local_dir}"
                )

            run_command(clone_command)

            # 确保目录已成功创建后再进入
            if not os.path.isdir(model_dir):
                print_color(f"无法找到 {model_dir} 目录，克隆可能失败", RED)
                return

            os.chdir(model_dir)
            ensure_ownership(model_dir)

            # 检查git lfs是否可用并处理文件
            try:
                for file in (
                    subprocess.check_output(["git", "lfs", "ls-files"])
                    .decode()
                    .splitlines()
                ):
                    file_path = file.split(" ")[-1]
                    file_full_path = os.path.join(model_dir, file_path)
                    os.makedirs(os.path.dirname(file_full_path), exist_ok=True)
                    open(file_full_path, "w").close()  # 截断文件
            except subprocess.CalledProcessError as e:
                print_color(f"获取LFS文件列表失败: {e}", RED)
                return

        include_patterns = args.include or []
        exclude_patterns = args.exclude or []

        def matches_patterns(file, patterns):
            return any(fnmatch.fnmatch(file, pattern) for pattern in patterns)

        try:
            files = (
                subprocess.check_output(["git", "lfs", "ls-files"])
                .decode()
                .splitlines()
            )
        except subprocess.CalledProcessError as e:
            print_color(f"获取LFS文件列表失败: {e}", RED)
            return

        files_to_download = []
        file_checks = []
        files_to_verify_after_download = []  # 新增：用于存储下载后需要校验的文件

        for file in files:
            parts = file.split(" ")
            if len(parts) < 3:
                continue

            partial_hash = parts[0]  # 获取 Git LFS 的部分哈希
            status = parts[1]  # 获取文件状态
            file_path = parts[-1]  # 获取文件路径

            is_downloaded = status == "*"
            # 使用 repo_id_for_url 生成下载链接
            url = get_download_url(
                endpoint, repo_id_for_url, file_path, args.source, args.dataset
            )

            if include_patterns and not matches_patterns(file_path, include_patterns):
                print_color(f"跳过 {file_path} (不匹配包含模式)", YELLOW)
                continue
            if exclude_patterns and matches_patterns(file_path, exclude_patterns):
                print_color(f"跳过 {file_path} (匹配排除模式)", YELLOW)
                continue

            # 修改这里的逻辑
            if args.verify_hash:
                if is_downloaded:
                    print_color(
                        f"文件 {file_path} 已下载，添加到哈希校验队列。", YELLOW
                    )
                    file_checks.append((file_path, partial_hash))
                else:
                    print_color(
                        f"文件 {file_path} 未下载，添加到下载队列，并将在下载后校验。",
                        YELLOW,
                    )
                    files_to_download.append(
                        (
                            url,
                            file_path,
                            args.tool,
                            args.x,
                            args.hf_token,
                            args.max_retries,
                        )
                    )
                    # 同时添加到下载后校验列表
                    files_to_verify_after_download.append((file_path, partial_hash))
            elif not is_downloaded:  # 如果未开启校验，只添加未下载的文件到下载列表
                print_color(f"文件 {file_path} 未下载，添加到下载队列。", YELLOW)
                files_to_download.append(
                    (url, file_path, args.tool, args.x, args.hf_token, args.max_retries)
                )
            else:  # 未开启校验且已下载
                print_color(f"文件 {file_path} 已经存在且未开启哈希验证，跳过。", GREEN)

        # 1. 校验已存在的文件 (如果开启了 --verify_hash)
        if file_checks:
            print_color("开始校验已存在文件的哈希...", BLUE)
            # ... (校验 file_checks 的代码保持不变) ...
            with ProcessPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(check_file_hash, file_info)
                    for file_info in file_checks
                ]
                # ... (处理 futures 的代码保持不变) ...
                for future in as_completed(futures):
                    file_path, is_valid = future.result()
                    if is_valid:
                        print_color(f"文件 {file_path} 校验通过。", GREEN)
                    else:
                        # 校验失败，需要重新下载
                        partial_hash = next(
                            info[1] for info in file_checks if info[0] == file_path
                        )
                        url = get_download_url(
                            endpoint,
                            repo_id_for_url,
                            file_path,
                            args.source,
                            args.dataset,
                        )
                        print_color(
                            f"文件 {file_path} 校验失败，添加到下载队列。", YELLOW
                        )
                        files_to_download.append(
                            (
                                url,
                                file_path,
                                args.tool,
                                args.x,
                                args.hf_token,
                                args.max_retries,
                            )
                        )
                        # 如果校验失败，也需要在下载后重新校验
                        if args.verify_hash:
                            files_to_verify_after_download.append(
                                (file_path, partial_hash)
                            )

        # 2. 下载需要下载或校验失败的文件
        total_files_to_download = len(files_to_download)
        if total_files_to_download == 0:
            print_color("没有需要下载的文件。", GREEN)
            # return # 不要在这里返回，因为可能还需要校验新下载的文件
        else:
            print_color(f"开始下载 {total_files_to_download} 个文件...", BLUE)
            start_time = time.time()
            completed_files = 0
            download_successful_files = []  # 记录成功下载的文件路径

            with ProcessPoolExecutor(max_workers=32) as executor:
                # ... (下载文件的 futures 提交代码保持不变) ...
                futures = [
                    executor.submit(
                        download_file, url, file_path, tool, threads, token, max_retries
                    )
                    for url, file_path, tool, threads, token, max_retries in files_to_download
                ]

                for i, future in enumerate(as_completed(futures)):
                    # 获取对应任务的文件路径
                    original_task_info = files_to_download[i]
                    current_file_path = original_task_info[1]
                    try:
                        elapsed_time = future.result()
                        if (
                            elapsed_time is not None
                        ):  # 假设 download_file 成功时返回时间，失败时返回 None 或抛异常
                            completed_files += 1
                            download_successful_files.append(
                                current_file_path
                            )  # 记录成功下载的文件
                            total_elapsed_time = time.time() - start_time
                            avg_time_per_file = (
                                total_elapsed_time / completed_files
                                if completed_files > 0
                                else 0
                            )
                            remaining_files = total_files_to_download - completed_files
                            estimated_remaining_time = (
                                avg_time_per_file * remaining_files
                            )

                            print_color(
                                f"已完成 {completed_files}/{total_files_to_download} 个文件下载。"
                                f"用时：{total_elapsed_time:.2f} 秒。"
                                f"预计剩余时间：{estimated_remaining_time:.2f} 秒。",
                                BLUE,
                            )
                        else:
                            print_color(
                                f"下载文件 {current_file_path} 失败 (未返回有效时间)。",
                                RED,
                            )
                    except Exception as e:
                        print_color(f"下载文件 {current_file_path} 失败: {e}", RED)

        # 3. 校验刚刚下载完成的文件 (如果开启了 --verify_hash)
        if args.verify_hash and files_to_verify_after_download:
            print_color("开始校验新下载文件的哈希...", BLUE)
            verification_needed_after_download = [
                item
                for item in files_to_verify_after_download
                if item[0] in download_successful_files  # 只校验成功下载的文件
            ]
            if verification_needed_after_download:
                with ProcessPoolExecutor(max_workers=4) as executor:
                    futures = [
                        executor.submit(check_file_hash, file_info)
                        for file_info in verification_needed_after_download
                    ]
                    for future in as_completed(futures):
                        file_path, is_valid = future.result()
                        if is_valid:
                            print_color(f"新下载的文件 {file_path} 校验通过。", GREEN)
                        else:
                            print_color(
                                f"新下载的文件 {file_path} 校验失败！请检查文件或重新下载。",
                                RED,
                            )
            else:
                print_color("没有成功下载的文件需要进行下载后校验。", YELLOW)

        if args.remove_git:
            git_dir = os.path.join(model_dir, ".git")
            if os.path.isdir(git_dir):
                print_color(f"移除 {git_dir} 目录以避免Git仓库嵌套问题。", YELLOW)
                user_input = input("是否继续? [y/N]: ").lower()
                if user_input == "y":
                    shutil.rmtree(git_dir)
                    print_color(f"已移除 {git_dir} 目录。", GREEN)
                else:
                    print_color(f"保留 {git_dir} 目录。", YELLOW)

        print_color("所有操作完成。", GREEN)
    except Exception as e:
        print_color(f"发生错误: {e}", RED)
    finally:
        # 恢复原始工作目录
        os.chdir(original_dir)


if __name__ == "__main__":
    main()
