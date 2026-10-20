"""Private S3 storage; blocking SDK operations are called via asyncio.to_thread."""

from urllib.parse import urlparse

import urllib3
from minio import Minio

from app.config import get_settings


class AudioStorage:
    def __init__(self):
        settings = get_settings()
        endpoint = urlparse(settings.minio_endpoint)
        self.bucket = settings.minio_bucket
        self.client = Minio(
            endpoint.netloc or endpoint.path,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=5, read=30), retries=False
            ),
        )

    def initialize(self):
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def upload(self, bucket, key, path):
        self.client.fput_object(bucket, key, str(path), content_type="audio/mpeg")

    def open(self, bucket, key, offset=0, length=0):
        return self.client.get_object(bucket, key, offset=offset, length=length)

    def delete(self, bucket, key):
        self.client.remove_object(bucket, key)

    def list_objects(self):
        return self.client.list_objects(self.bucket, prefix="audio/", recursive=True)

    def ready(self):
        return self.client.bucket_exists(self.bucket)


def get_audio_storage():
    return AudioStorage()


if __name__ == "__main__":
    AudioStorage().initialize()
