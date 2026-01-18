"""
EvoAlpha OS - 同步模块：R2管理器
上传CSV文件到Cloudflare R2对象存储
"""

import boto3
from loguru import logger
from pathlib import Path
from typing import List
import os


class R2Manager:
    """Cloudflare R2 管理器"""

    def __init__(
        self,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
    ):
        """
        初始化R2客户端

        Args:
            account_id: Cloudflare账号ID
            access_key_id: R2访问密钥ID
            secret_access_key: R2密钥
            bucket_name: 存储桶名称
        """
        # 构建R2端点
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

        # 创建S3客户端（R2兼容S3 API）
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )
        self.bucket_name = bucket_name

    def upload_file(self, file_path: str, key: str = None) -> str:
        """
        上传文件到R2

        Args:
            file_path: 本地文件路径
            key: R2对象键，如果为空则使用文件名

        Returns:
            R2对象URL
        """
        file_path = Path(file_path)

        if key is None:
            key = file_path.name

        logger.info(f"开始上传文件到R2: {file_path.name}")

        try:
            # 上传文件
            self.s3_client.upload_file(
                str(file_path), self.bucket_name, key, ExtraArgs={"ACL": "public-read"}
            )

            # 构建公开URL
            url = f"https://{self.bucket_name}.{self.s3_client._endpoint.host}/{key}"

            logger.success(f"上传成功: {url}")
            return url

        except Exception as e:
            logger.error(f"上传文件失败: {e}")
            raise

    def upload_files(self, file_paths: List[str], prefix: str = "") -> List[str]:
        """
        批量上传文件

        Args:
            file_paths: 文件路径列表
            prefix: R2对象键前缀

        Returns:
            R2对象URL列表
        """
        logger.info(f"开始批量上传 {len(file_paths)} 个文件")

        urls = []
        for file_path in file_paths:
            try:
                key = f"{prefix}/{Path(file_path).name}" if prefix else None
                url = self.upload_file(file_path, key)
                urls.append(url)
            except Exception as e:
                logger.error(f"上传文件 {file_path} 失败: {e}")
                continue

        logger.success(f"批量上传完成，共 {len(urls)} 个文件")
        return urls

    def list_objects(self, prefix: str = "") -> List[str]:
        """
        列出R2桶中的对象

        Args:
            prefix: 对象键前缀

        Returns:
            对象键列表
        """
        logger.info(f"列出R2桶对象: {self.bucket_name}/{prefix}")

        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name, Prefix=prefix
            )

            objects = [obj["Key"] for obj in response.get("Contents", [])]
            logger.info(f"找到 {len(objects)} 个对象")

            return objects

        except Exception as e:
            logger.error(f"列出对象失败: {e}")
            raise

    def delete_object(self, key: str):
        """
        删除R2对象

        Args:
            key: 对象键
        """
        logger.info(f"删除R2对象: {key}")

        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)
            logger.success(f"删除成功: {key}")

        except Exception as e:
            logger.error(f"删除对象失败: {e}")
            raise


if __name__ == "__main__":
    # 测试R2连接（需要配置环境变量）
    manager = R2Manager(
        account_id=os.getenv("R2_ACCOUNT_ID"),
        access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        bucket_name=os.getenv("R2_BUCKET_NAME"),
    )

    # 列出对象
    objects = manager.list_objects()
    logger.info(f"R2桶中的对象: {objects}")
