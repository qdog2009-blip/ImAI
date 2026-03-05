"""
文件上传 API。

POST /api/upload/image   — 上传图片
POST /api/upload/video   — 上传视频
POST /api/upload/audio   — 上传音频
POST /api/upload/file    — 上传通用文件
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, HTTPException, status
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/api/upload")


# 确保上传目录存在
def _get_upload_dir() -> Path:
    """获取上传目录，不存在则创建。"""
    upload_dir = Path(settings.LOCAL_STORAGE_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _save_file(file: UploadFile, sub_dir: str, base_url: str = "") -> dict:
    """
    保存上传的文件，返回文件信息。
    
    Args:
        file: 上传的文件
        sub_dir: 子目录名 (image/video/audio/file)
        base_url: 基础URL，用于构建完整访问地址
    
    Returns:
        包含 url、size 等信息的字典
    """
    # 获取文件扩展名
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()
    
    # 生成唯一文件名
    unique_name = f"{uuid.uuid4().hex}{ext}"
    
    # 保存到对应子目录
    upload_dir = _get_upload_dir() / sub_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = upload_dir / unique_name
    
    # 写入文件
    content = file.file.read()
    file_size = len(content)
    
    with open(file_path, "wb") as f:
        f.write(content)
    
    # 构建访问 URL - 使用完整URL
    if base_url:
        # 移除末尾的斜杠
        base_url = base_url.rstrip('/')
        full_url = f"{base_url}/uploads/{sub_dir}/{unique_name}"
    else:
        full_url = f"/uploads/{sub_dir}/{unique_name}"
    
    return {
        "url": full_url,
        "size": file_size,
        "file_name": filename,
    }


# ================================================================== #
#  上传响应模型
# ================================================================== #

class UploadResponse(BaseModel):
    url: str
    size: int | None = None
    file_name: str | None = None
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    duration: int | None = None


# ================================================================== #
#  上传端点
# ================================================================== #

@router.post("/image", response_model=UploadResponse)
async def upload_image(file: UploadFile = File(...)) -> UploadResponse:
    """上传图片。"""
    # 验证文件类型
    allowed_image_types = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    ext = Path(file.filename or "").suffix.lower()
    
    if ext not in allowed_image_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的图片类型: {ext}",
        )
    
    result = _save_file(file, "image")
    return UploadResponse(**result)


@router.post("/video", response_model=UploadResponse)
async def upload_video(file: UploadFile = File(...)) -> UploadResponse:
    """上传视频。"""
    # 验证文件类型
    allowed_video_types = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}
    ext = Path(file.filename or "").suffix.lower()
    
    if ext not in allowed_video_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的视频类型: {ext}",
        )
    
    result = _save_file(file, "video")
    return UploadResponse(**result)


@router.post("/audio", response_model=UploadResponse)
async def upload_audio(file: UploadFile = File(...)) -> UploadResponse:
    """上传音频。"""
    # 验证文件类型
    allowed_audio_types = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}
    ext = Path(file.filename or "").suffix.lower()
    
    if ext not in allowed_audio_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的音频类型: {ext}",
        )
    
    result = _save_file(file, "audio")
    return UploadResponse(**result)


@router.post("/file", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)) -> UploadResponse:
    """上传通用文件。"""
    result = _save_file(file, "file")
    return UploadResponse(**result)