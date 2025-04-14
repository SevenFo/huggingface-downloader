import os
import hashlib
import time
import urllib.parse
from concurrent.futures import ProcessPoolExecutor, as_completed
import subprocess  # 需要用于 git lfs ls-files

# 从同级模块导入
from .utils import print_color, run_command, RED, GREEN, YELLOW, BLUE


def is_file_downloaded(file_path, expected_hash):
    """校验文件哈希值的前10位是否与预期匹配。"""
    if not os.path.isfile(file_path):
        return False
    if expected_hash is None:  # 如果没有提供预期哈希，则无法校验
        return False

    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        file_hash = sha256.hexdigest()
        # 比较 LFS 指针中的哈希前缀
        return file_hash.startswith(expected_hash)
    except OSError as e:
        print_color(f"读取文件 {file_path} 时出错: {e}", RED)
        return False


def download_file(url, file_path, tool, threads, token=None, max_retries=10):
    """下载单个文件，支持 aria2c 和 wget，带重试逻辑。"""
    if not file_path:
        print_color("错误: 收到空文件路径", RED)
        return None  # 返回 None 表示失败

    # 确保目录存在
    dir_path = os.path.dirname(os.path.abspath(file_path))
    filename = os.path.basename(file_path)
    os.makedirs(dir_path, exist_ok=True)

    # 构建命令
    headers = []
    if token:
        headers.append(f'"Authorization: Bearer {token}"')

    if tool == "wget":
        header_str = " ".join([f"--header={h}" for h in headers])
        command = f'wget --continue {header_str} "{url}" -O "{file_path}"'
    else:  # aria2c (默认)
        header_str = " ".join([f"--header={h}" for h in headers])
        # 优化 aria2c 参数
        command = (
            f"aria2c --console-log-level=warn --summary-interval=0 "
            f"--file-allocation=none -x {threads} -s {threads} -k 1M "
            f'--continue=true {header_str} "{url}" '
            f'-d "{dir_path}" -o "{filename}"'
        )

    # 重试下载
    for attempt in range(1, max_retries + 1):
        try:
            print_color(
                f"开始下载 {file_path} (第 {attempt}/{max_retries} 次尝试)", YELLOW
            )
            start_time = time.time()
            run_command(command)  # run_command 会在失败时抛出异常
            elapsed_time = time.time() - start_time
            # 简单检查文件是否存在作为成功的初步判断
            if os.path.exists(file_path):
                print_color(
                    f"成功下载 {file_path}，用时 {elapsed_time:.2f} 秒。", GREEN
                )
                return elapsed_time  # 返回下载时间表示成功
            else:
                # 如果 run_command 没抛异常但文件不存在，也算失败
                raise Exception("下载命令执行后文件未找到")

        except Exception as e:
            print_color(f"下载 {file_path} 第 {attempt} 次尝试失败: {e}", RED)
            if attempt == max_retries:
                print_color(f"在 {max_retries} 次尝试后未能下载 {file_path}。", RED)
                return None  # 返回 None 表示最终失败
            else:
                sleep_time = 2**attempt  # 指数退避
                print_color(f"休眠 {sleep_time} 秒后重试...", YELLOW)
                time.sleep(sleep_time)
    return None  # 理论上不会执行到这里，但为了清晰


def check_file_hash(file_info):
    """用于 ProcessPoolExecutor 的包装函数，检查文件哈希。"""
    file_path, expected_hash = file_info
    # 确保文件路径是绝对路径或相对于当前工作目录正确
    # 注意：ProcessPoolExecutor 的工作目录可能与主进程不同
    # 最好在传递 file_info 前就确保路径正确
    is_valid = is_file_downloaded(file_path, expected_hash)
    return (file_path, is_valid)


def get_download_url(endpoint, repo_id_for_url, file_path, source, is_dataset):
    """根据源平台生成 LFS 文件的下载 URL。"""
    encoded_file_path = urllib.parse.quote(file_path, safe="")
    if source == "huggingface":
        # Hugging Face URL 结构: {endpoint}/{repo_id}/resolve/{revision}/{path}
        revision = "main"  # 或者可以设为可配置
        # repo_id_for_url 应该是 'org/repo' 或 'datasets/org/repo'
        return f"{endpoint}/{repo_id_for_url}/resolve/{revision}/{encoded_file_path}"
    elif source == "modelscope":
        # ModelScope URL 结构: {endpoint}/api/v1/{type}/{repo_id}/repo?Revision={revision}&FilePath={path}
        api_base = "datasets" if is_dataset else "models"
        revision = "master"  # 或者可以设为可配置
        # repo_id_for_url 应该是 'org/repo'
        return f"{endpoint}/api/v1/{api_base}/{repo_id_for_url}/repo?Revision={revision}&FilePath={encoded_file_path}"
    else:
        raise ValueError(f"不支持的源: {source}")


def get_lfs_files(repo_dir):
    """获取 Git LFS 文件列表及其状态和哈希。"""
    files_info = []
    try:
        # 确保在正确的目录下执行
        result = subprocess.check_output(
            ["git", "lfs", "ls-files", "--long"],  # 使用 --long 获取完整哈希
            cwd=repo_dir,
            text=True,
            stderr=subprocess.PIPE,
        )
        lines = result.strip().splitlines()
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                lfs_hash = parts[0]  # OID
                status = parts[1]  # '*' downloaded, '-' not downloaded
                file_path = " ".join(parts[2:])  # 文件名可能包含空格
                files_info.append(
                    {
                        "hash": lfs_hash,
                        "status": status,
                        "path": file_path,
                        "abs_path": os.path.abspath(
                            os.path.join(repo_dir, file_path)
                        ),  # 计算绝对路径
                    }
                )
    except subprocess.CalledProcessError as e:
        print_color(f"获取 LFS 文件列表失败: {e}", RED)
        if e.stderr:
            print_color(f"Git LFS 错误信息: {e.stderr}", RED)
        return None  # 返回 None 表示失败
    except FileNotFoundError:
        print_color("错误: 'git' 命令未找到。", RED)
        return None
    return files_info


def create_lfs_placeholders(lfs_files_info, repo_dir):
    """为 LFS 文件创建空的占位文件和必要的目录。"""
    if lfs_files_info is None:
        return
    print_color("创建 LFS 文件占位符...", BLUE)
    for file_info in lfs_files_info:
        file_full_path = file_info["abs_path"]
        try:
            dir_name = os.path.dirname(file_full_path)
            if not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)
            # 只创建空文件，不截断现有文件（如果已存在）
            if not os.path.exists(file_full_path):
                with open(file_full_path, "w") as f:
                    pass  # 创建空文件
        except OSError as e:
            print_color(f"创建占位文件 {file_full_path} 或目录失败: {e}", RED)
