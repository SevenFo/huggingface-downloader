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
    return parser.parse_args()


def check_command(command):
    if shutil.which(command) is None:
        print_color(f"{command} 未安装，请先安装。", RED)
        sys.exit(1)


def ensure_ownership(repo_dir):
    try:
        subprocess.check_output(
            ["git", "status"], cwd=repo_dir, stderr=subprocess.STDOUT
        )
    except subprocess.CalledProcessError as e:
        if "detected dubious ownership" in e.output.decode():
            subprocess.run(
                ["git", "config", "--global", "--add", "safe.directory", repo_dir]
            )
            print_color(f"已将 {repo_dir} 标记为 git 安全目录。", YELLOW)


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
    dir_path = os.path.dirname(file_path)
    os.makedirs(dir_path, exist_ok=True)

    if tool == "wget":
        command = f'wget -c "{url}" -O "{file_path}"'
        if token:
            command = f'wget --header="Authorization: Bearer {token}" -c "{url}" -O "{file_path}"'
    else:  # aria2c
        command = f'aria2c --console-log-level=error --file-allocation=none -x {threads} -s {threads} -k 1M -c "{url}" -d "{dir_path}" -o "{os.path.basename(file_path)}"'
        if token:
            command = f'aria2c --header="Authorization: Bearer {token}" --console-log-level=error --file-allocation=none -x {threads} -s {threads} -k 1M -c "{url}" -d "{dir_path}" -o "{os.path.basename(file_path)}"'

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

    hf_endpoint = os.getenv("HF_ENDPOINT", "https://huggingface.co")
    repo_id = f"datasets/{args.repo_id}" if args.dataset else args.repo_id
    local_dir = args.local_dir or repo_id.split("/")[-1]

    if os.path.isdir(os.path.join(local_dir, ".git")):
        print_color(f"{local_dir} 已存在，跳过克隆。", YELLOW)
        os.chdir(local_dir)
        ensure_ownership(local_dir)
        run_command("GIT_LFS_SKIP_SMUDGE=1 git pull")
    else:
        repo_url = f"{hf_endpoint}/{repo_id}"
        if args.hf_username and args.hf_token:
            repo_url = (
                f"https://{args.hf_username}:{args.hf_token}@{hf_endpoint}/{repo_id}"
            )

        run_command(f"GIT_LFS_SKIP_SMUDGE=1 git clone {repo_url} {local_dir}")
        os.chdir(local_dir)
        ensure_ownership(local_dir)

        for file in (
            subprocess.check_output(["git", "lfs", "ls-files"]).decode().splitlines()
        ):
            file_path = file.split(" ")[-1]
            open(file_path, "w").close()  # 截断文件

    include_patterns = args.include or []
    exclude_patterns = args.exclude or []

    def matches_patterns(file, patterns):
        return any(fnmatch.fnmatch(file, pattern) for pattern in patterns)

    files = subprocess.check_output(["git", "lfs", "ls-files"]).decode().splitlines()
    files_to_download = []
    file_checks = []

    for file in files:
        file_path = file.split(" ")[-1]
        partial_hash = file.split(" ")[0]  # 获取 Git LFS 的部分哈希
        is_downloaded = file.split(" ")[1] == "*"
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
            files_to_download.append((url, file_path, args.tool, args.x, args.hf_token))
        else:
            print_color(f"文件 {file_path} 已经存在且未开启哈希验证，跳过下载。", GREEN)

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
                    (url, file_path, args.tool, args.x, args.hf_token, args.max_retries)
                )

    total_files_to_download = len(files_to_download)
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

    print_color("下载完成。", GREEN)


if __name__ == "__main__":
    main()
