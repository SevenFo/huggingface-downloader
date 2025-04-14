import os
import sys
import subprocess
import shutil
import signal

# 终端输出颜色定义
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
NC = "\033[0m"  # 无颜色
BLUE = "\033[0;34m"

# 全局列表来跟踪子进程 - 注意：在多进程模型下，这个全局列表可能不如预期工作
# 考虑将进程管理移到调用 run_command 的地方
subprocesses = []


def print_color(message, color=NC):
    """打印带颜色的消息到终端。"""
    print(f"{color}{message}{NC}")


def check_command(command):
    """检查命令是否存在于系统中。"""
    if shutil.which(command) is None:
        print_color(f"{command} 未安装，请先安装。", RED)
        sys.exit(1)


def ensure_ownership(repo_dir):
    """确保 Git 仓库目录的所有权，避免 'dubious ownership' 错误。"""
    # 确保目录存在
    if not os.path.isdir(repo_dir):
        print_color(f"目录 {repo_dir} 不存在，无法确保所有权", RED)
        return

    # 检查是否为有效的 Git 仓库目录
    git_dir_path = os.path.join(repo_dir, ".git")
    if not os.path.isdir(git_dir_path):
        # 如果不是 Git 仓库（例如已被移除），则无需检查所有权
        # print_color(f"{repo_dir} 不是一个有效的 Git 仓库目录，跳过所有权检查。", YELLOW)
        return

    try:
        # 使用更安全的方式检查，避免打印过多输出
        subprocess.check_output(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_dir,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as e:
        # 如果命令失败，检查是否是所有权问题
        output = e.output.decode() if hasattr(e, "output") and e.output else ""
        if "detected dubious ownership" in output:
            try:
                # 尝试添加为安全目录
                subprocess.run(
                    ["git", "config", "--global", "--add", "safe.directory", repo_dir],
                    check=True,
                    capture_output=True,
                )
                print_color(f"已将 {repo_dir} 添加到 Git 安全目录。", YELLOW)
            except subprocess.CalledProcessError as add_safe_err:
                print_color(
                    f"尝试将 {repo_dir} 添加为安全目录失败: {add_safe_err}", RED
                )
        else:
            # 如果不是所有权问题，打印原始错误
            print_color(f"在 {repo_dir} 检查 Git 状态时出错: {e}", RED)
            if output:
                print_color(f"Git 输出: {output}", RED)


def run_command(command, cwd=None):
    """运行 shell 命令并等待其完成。"""
    print_color(f"运行命令: {command}", BLUE)
    # 注意：直接使用 shell=True 有安全风险，如果可能应避免
    # 考虑将命令拆分为列表传递给 Popen
    process = subprocess.Popen(command, shell=True, cwd=cwd)
    # subprocesses.append(process) # 移除全局列表跟踪
    process.wait()
    if process.returncode != 0:
        # 可以考虑在这里捕获更详细的错误信息
        raise Exception(f"命令失败 (返回码 {process.returncode}): {command}")
    return process  # 返回进程对象，如果需要的话


def signal_handler(sig, frame):
    """处理终止信号 (例如 Ctrl+C)。"""
    print_color("\n接收到终止信号。正在尝试优雅地停止...", RED)
    # 注意：直接终止子进程可能导致文件损坏或状态不一致
    # 更好的方法是让子任务（如下载）能够响应中断信号
    # 这里仅作示例性退出
    sys.exit(0)


def is_inside_git_repo(path="."):
    """检查指定路径是否在 Git 工作树内。"""
    try:
        subprocess.check_output(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            stderr=subprocess.STDOUT,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
