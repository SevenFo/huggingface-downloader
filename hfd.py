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
        description="从 Hugging Face 使用提供的 repo ID 下载模型或数据集。"
    )
    parser.add_argument(
        "repo_id", help="Hugging Face repo ID，格式为 'org/repo_name'。"
    )
    parser.add_argument("--include", nargs="+", help="要包含下载的文件模式。")
    parser.add_argument(
        "--exclude",
        nargs="+",
        help="要排除下载的文件模式。",
    )
    parser.add_argument("--hf_username", help="Hugging Face 用户名，用于认证。")
    parser.add_argument("--hf_token", help="Hugging Face 令牌，用于认证。")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("HF_ENDPOINT", "https://huggingface.co"),
        help="Hugging Face 端点，默认为环境变量 HF_ENDPOINT 或 https://huggingface.co",
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


def main():
    signal.signal(signal.SIGINT, signal_handler)
    args = parse_args()

    check_command("git")
    check_command("git-lfs")
    check_command(args.tool)

    # 使用传入的endpoint参数
    hf_endpoint = args.endpoint
    repo_id = f"datasets/{args.repo_id}" if args.dataset else args.repo_id
    local_dir = args.local_dir or repo_id.split("/")[-1]

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
            repo_url = f"{hf_endpoint}/{repo_id}"
            if args.hf_username and args.hf_token:
                repo_url = f"https://{args.hf_username}:{args.hf_token}@{hf_endpoint.replace('https://', '')}/{repo_id}"

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

        for file in files:
            parts = file.split(" ")
            if len(parts) < 3:
                continue

            partial_hash = parts[0]  # 获取 Git LFS 的部分哈希
            status = parts[1]  # 获取文件状态
            file_path = parts[-1]  # 获取文件路径

            is_downloaded = status == "*"
            url = f"{hf_endpoint}/{repo_id}/resolve/main/{file_path}"

            if include_patterns and not matches_patterns(file_path, include_patterns):
                print_color(f"跳过 {file_path} (不匹配包含模式)", YELLOW)
                continue
            if exclude_patterns and matches_patterns(file_path, exclude_patterns):
                print_color(f"跳过 {file_path} (匹配排除模式)", YELLOW)
                continue
            if args.verify_hash and is_downloaded:
                print_color(f"文件 {file_path} 已下载，添加到哈希校验队列。", YELLOW)
                file_checks.append((file_path, partial_hash))
            elif not is_downloaded:
                print_color(f"文件 {file_path} 未下载，添加到下载队列。", YELLOW)
                files_to_download.append(
                    (url, file_path, args.tool, args.x, args.hf_token, args.max_retries)
                )
            else:
                print_color(
                    f"文件 {file_path} 已经存在且未开启哈希验证，跳过下载。", GREEN
                )

        # 后续代码不变...
        total_files = len(file_checks)
        start_time = time.time()
        completed_files = 0

        # 多进程校验文件哈希
        with ProcessPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(check_file_hash, file_info) for file_info in file_checks
            ]

            for future in as_completed(futures):
                file_path, is_downloaded = future.result()
                if is_downloaded:
                    print_color(f"文件 {file_path} 校验通过。", GREEN)
                else:
                    partial_hash = next(
                        info[1] for info in file_checks if info[0] == file_path
                    )
                    url = f"{hf_endpoint}/{repo_id}/resolve/main/{file_path}"
                    print_color(f"文件 {file_path} 校验失败，添加到下载队列。", YELLOW)
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

        total_files_to_download = len(files_to_download)
        if total_files_to_download == 0:
            print_color("没有需要下载的文件。", GREEN)
            return

        start_time = time.time()
        completed_files = 0

        with ProcessPoolExecutor(max_workers=32) as executor:
            futures = [
                executor.submit(
                    download_file, url, file_path, tool, threads, token, max_retries
                )
                for url, file_path, tool, threads, token, max_retries in files_to_download
            ]

            for future in as_completed(futures):
                try:
                    elapsed_time = future.result()
                    completed_files += 1
                    total_elapsed_time = time.time() - start_time
                    avg_time_per_file = total_elapsed_time / completed_files
                    remaining_files = total_files_to_download - completed_files
                    estimated_remaining_time = avg_time_per_file * remaining_files

                    print_color(
                        f"已完成 {completed_files}/{total_files_to_download} 个文件。"
                        f"用时：{total_elapsed_time:.2f} 秒。"
                        f"预计剩余时间：{estimated_remaining_time:.2f} 秒。",
                        BLUE,
                    )
                except Exception as e:
                    print_color(f"下载文件失败: {e}", RED)

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

        print_color("下载完成。", GREEN)
    except Exception as e:
        print_color(f"发生错误: {e}", RED)
    finally:
        # 恢复原始工作目录
        os.chdir(original_dir)


if __name__ == "__main__":
    main()
