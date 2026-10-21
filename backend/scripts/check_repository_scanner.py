"""检查代码仓库扫描工具能否读取示例订单系统。"""

from pathlib import Path

from app.tools.repository_scanner import scan_repository

# 当前文件位于 backend/scripts 目录。
# parents[2] 对应整个 ChangePilot-AI 项目的根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 指定本次需要扫描的示例订单仓库。
SAMPLE_REPOSITORY_PATH = PROJECT_ROOT / "sample_repos" / "order_service"


def main() -> None:
    """扫描示例订单仓库并输出扫描摘要。"""

    print("正在扫描示例订单仓库……")
    print(f"仓库路径：{SAMPLE_REPOSITORY_PATH}\n")

    # 调用我们之前开发的安全仓库扫描工具。
    snapshot = scan_repository(SAMPLE_REPOSITORY_PATH)

    print(f"仓库名称：{snapshot.repository_name}")
    print(f"扫描文件数：{snapshot.scanned_file_count}")
    print(f"是否被截断：{snapshot.truncated}\n")

    print("成功读取的文件：")

    # 这里只输出文件信息，不在终端打印完整源代码，
    # 避免大型仓库的终端输出过多。
    for repository_file in snapshot.files:
        print(
            f"- {repository_file.relative_path} "
            f"[{repository_file.language}, "
            f"{repository_file.size_bytes} bytes]"
        )


if __name__ == "__main__":
    main()
