import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Enum, ForeignKey, JSON, Float
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class VideoStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING_SCENES = "generating_scenes"
    GENERATING_CLIPS = "generating_clips"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"


class PublishStatus(str, enum.Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    PUBLISHED = "published"
    FAILED = "failed"
    SCHEDULED = "scheduled"


class Platform(str, enum.Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"


class AppUser(Base):
    __tablename__ = "auth_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(320), nullable=False, unique=True, index=True)
    display_name = Column(String(255), nullable=False)
    password_hash = Column(Text)
    auth_source = Column(String(20), nullable=False, default="local")
    external_user_id = Column(String(255), index=True)
    google_sub = Column(String(255), unique=True)
    role = Column(String(50), nullable=False, default="user")
    is_active = Column(Boolean, nullable=False, default=True)
    email_verified = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login_at = Column(DateTime)


class VideoProject(Base):
    __tablename__ = "video_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    track_id = Column(Integer, nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    tags = Column(JSON, default=list)  # ["worship", "gospel"]
    style_prompt = Column(Text, default="")  # visual style hint
    aspect_ratio = Column(String(10), default="16:9")
    status = Column(Enum(VideoStatus), default=VideoStatus.PENDING)
    error_message = Column(Text)
    progress = Column(Integer, default=0)  # 0-100

    # Track metadata (copied from levita)
    track_title = Column(String(500))
    track_artist = Column(String(500))
    track_duration = Column(Float)
    lyrics_text = Column(Text)
    lyrics_words = Column(JSON)  # word-level timestamps
    audio_path = Column(Text)  # path to audio file
    use_custom_images = Column(Boolean, default=False)  # user uploaded own photos
    use_custom_video = Column(Boolean, default=False)  # user uploaded own video
    enable_subtitles = Column(Boolean, default=True)  # subtitle toggle
    zoom_images = Column(Boolean, default=True)  # enable zoom effect on still images
    image_display_seconds = Column(Float, default=0)  # custom seconds per image (0=auto)
    no_background_music = Column(Boolean, default=False)  # disable background music entirely
    is_karaoke = Column(Boolean, default=False)  # karaoke mode: single background image

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    scenes = relationship("VideoScene", back_populates="project", cascade="all, delete-orphan")
    renders = relationship("VideoRender", back_populates="project", cascade="all, delete-orphan")


class VideoScene(Base):
    __tablename__ = "video_scenes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("video_projects.id", ondelete="CASCADE"), nullable=False)
    scene_index = Column(Integer, nullable=False)  # order in video
    scene_type = Column(String(20), default="image")  # "image" or "video_clip"
    prompt = Column(Text)  # prompt used for generation
    image_path = Column(Text)  # local path to generated image
    clip_path = Column(Text)  # local path to generated video clip (Grok)
    is_user_uploaded = Column(Boolean, default=False)  # user-uploaded image
    start_time = Column(Float)  # seconds in audio
    end_time = Column(Float)  # seconds in audio
    lyrics_segment = Column(Text)  # lyrics for this scene
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("VideoProject", back_populates="scenes")


class VideoRender(Base):
    __tablename__ = "video_renders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("video_projects.id", ondelete="CASCADE"), nullable=False)
    format = Column(String(10), default="16:9")  # "16:9" or "9:16"
    file_path = Column(Text)
    file_size = Column(Integer)  # bytes
    thumbnail_path = Column(Text)
    duration = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("VideoProject", back_populates="renders")


class ImageBank(Base):
    __tablename__ = "image_bank"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    tags = Column(ARRAY(Text), default=list)  # ["sunset", "ocean", "warm", "beach"]
    style = Column(Text, default="")  # style_hint used during generation
    aspect_ratio = Column(String(10), default="16:9")
    prompt = Column(Text, default="")  # original visual_prompt
    file_path = Column(Text, nullable=False)  # absolute path to image
    reuse_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class SocialAccount(Base):
    __tablename__ = "social_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    platform = Column(Enum(Platform), nullable=False)
    account_label = Column(String(255))
    platform_user_id = Column(String(255))
    platform_username = Column(String(255))
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text)
    token_expires_at = Column(DateTime)
    extra_data = Column(JSON, default=dict)  # channel_id for youtube, etc.
    publish_links = Column(Text, default="")
    connected_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PublishJob(Base):
    __tablename__ = "publish_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    render_id = Column(Integer, ForeignKey("video_renders.id"), nullable=False)
    platform = Column(Enum(Platform), nullable=False)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id"), nullable=False)
    status = Column(Enum(PublishStatus), default=PublishStatus.PENDING)
    title = Column(String(500))
    description = Column(Text)
    tags = Column(JSON, default=list)
    scheduled_at = Column(DateTime)  # null = publish now
    published_at = Column(DateTime)
    platform_post_id = Column(String(255))  # video ID on platform
    platform_url = Column(Text)  # URL on platform
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    render = relationship("VideoRender")
    social_account = relationship("SocialAccount")


class PublishSchedule(Base):
    __tablename__ = "publish_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    platform = Column(Enum(Platform), nullable=False)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id"), nullable=False)
    frequency = Column(String(20), default="daily")  # "daily", "weekly"
    time_utc = Column(String(5), default="14:00")  # HH:MM
    day_of_week = Column(Integer)  # 0=Mon for weekly, null for daily
    is_active = Column(Boolean, default=True)
    queue = Column(JSON, default=list)  # list of render_ids to publish
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    social_account = relationship("SocialAccount")


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    voice_type = Column(String(20), nullable=False, default="builtin")  # "builtin" or "custom"
    # Built-in voice identifier (onyx, echo, nova, etc.)
    builtin_voice = Column(String(50))
    # OpenAI custom voice ID (returned by /v1/audio/voices)
    openai_voice_id = Column(String(255))
    # Path to the original voice sample uploaded by user
    sample_path = Column(Text)
    # TTS instructions for gpt-4o-mini-tts (tone, speed, accent, etc.)
    tts_instructions = Column(Text, default="")
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AutoSchedule(Base):
    __tablename__ = "auto_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(Text, nullable=False)
    video_type = Column(String(20), default="narration")  # "narration" | "music"
    creation_mode = Column(String(20), default="auto")  # "auto" | "manual"
    platform = Column(String(20), default="youtube")
    social_account_id = Column(Integer, ForeignKey("social_accounts.id"))
    frequency = Column(String(20), default="daily")
    time_utc = Column(String(5), default="14:00")
    day_of_week = Column(Integer, default=0)
    default_settings = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    themes = relationship("AutoScheduleTheme", back_populates="schedule", cascade="all, delete-orphan")
    social_account = relationship("SocialAccount")


class AutoScheduleTheme(Base):
    __tablename__ = "auto_schedule_themes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    auto_schedule_id = Column(Integer, ForeignKey("auto_schedules.id", ondelete="CASCADE"), nullable=False)
    theme = Column(Text, nullable=False)
    custom_settings = Column(JSON)
    status = Column(String(20), default="pending")  # "pending" | "processing" | "completed" | "failed"
    video_project_id = Column(Integer, ForeignKey("video_projects.id"))
    error_message = Column(Text)
    position = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    schedule = relationship("AutoSchedule", back_populates="themes")
