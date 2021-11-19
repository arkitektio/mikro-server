"""
Django settings for elements project.

Generated by 'django-admin startproject' using Django 3.1.7.

For more information on this file, see
https://docs.djangoproject.com/en/3.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/3.1/ref/settings/
"""
import os
from pathlib import Path
from omegaconf import OmegaConf


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
conf = OmegaConf.load(os.path.join(BASE_DIR, "config.yaml"))

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = conf.security.secret_key

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = conf.server.debug or False

ALLOWED_HOSTS = conf.server.hosts


ELEMENTS_HOST = "p-tnagerl-lab1"
ELEMENTS_INWARD = "elements"  # Set this to the host you are on
ELEMENTS_PORT = 8080  # Set this to the host you are on

CORS_ALLOW_ALL_ORIGINS = True
# S3 Settings


S3_PUBLIC_DOMAIN = f"{conf.s3.public.host}:{conf.s3.public.port}"  # TODO: FIx
AWS_ACCESS_KEY_ID = conf.s3.access_key
AWS_SECRET_ACCESS_KEY = conf.s3.secret_key
AWS_S3_ENDPOINT_URL = f"{conf.s3.protocol}://{conf.s3.host}:{conf.s3.port}"
AWS_S3_PUBLIC_ENDPOINT_URL = (
    f"{conf.s3.public.protocol}://{conf.s3.public.host}:{conf.s3.public.port}"
)
AWS_S3_URL_PROTOCOL = f"{conf.s3.public.protocol}:"
AWS_S3_FILE_OVERWRITE = False
AWS_QUERYSTRING_EXPIRE = 3600


AWS_STORAGE_BUCKET_NAME = "media"
AWS_DEFAULT_ACL = "private"
AWS_S3_USE_SSL = True
AWS_S3_SECURE_URLS = False  # Should resort to True if using in Production behind TLS
DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"

# Application definition
ARKITEKT_SERVICE = {
    "NAME": "elements",
    "VERSION": "0.1",
    "NEEDS_NEGOTIATION": True,
}


LOK = {
    "PUBLIC_KEY": conf.herre.public_key,
    "KEY_TYPE": conf.herre.key_type,
    "ISSUER": conf.herre.issuer,
}

SUPERUSERS = [
    {"USERNAME": su.username, "EMAIL": su.email, "PASSWORD": su.password}
    for su in conf.security.admins
]

GRUNNLAG = {"GROUPS": None}

STATIC_ROOT = "/var/www/static"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "django_filters",
    "taggit",
    "channels",
    "health_check",
    "health_check.db",
    # "health_check.contrib.psutil",
    # "health_check.contrib.s3boto3_storage",
    "health_check.contrib.redis",
    "lok",
    "guardian",
    "graphene_django",
    "rest_framework",
    "balder",
    "matrise",
    "grunnlag",
    "bord",
]

HEALTH_CHECK = {
    "DISK_USAGE_MAX": 90,  # percent
    "MEMORY_MIN": 100,  # in MB
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "lok.middlewares.request.jwt.JWTTokenMiddleWare",
    "lok.middlewares.request.bouncer.BouncedMiddleware",  # needs to be after JWT and session
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "elements.urls"


MATRISE = {
    "FILE_VERSION": "0.1",
    "API_VERSION": "0.1",
    "ACCESS_KEY": conf.s3.access_key,
    "SECRET_KEY": conf.s3.secret_key,
    "PUBLIC_URL": f"{conf.s3.public.host}:{conf.s3.public.port}",  # TODO: FIx
    "PRIVATE_URL": f"{conf.s3.protocol}://{conf.s3.host}:{conf.s3.port}",  # TODO: FIx
    "STORAGE_CLASS": "matrise.storages.s3.S3Storage",
    "GENERATOR_CLASS": "matrise.generators.default.DefaultPathGenerator",
    "BUCKET": "zarr",
}

BORD = {
    "FILE_VERSION": "0.1",
    "API_VERSION": "0.1",
    "ACCESS_KEY": conf.s3.access_key,
    "SECRET_KEY": conf.s3.secret_key,
    "PUBLIC_URL": f"{conf.s3.public.host}:{conf.s3.public.port}",  # TODO: FIx
    "PRIVATE_URL": f"{conf.s3.protocol}://{conf.s3.host}:{conf.s3.port}",  # TODO
    "STORAGE_CLASS": "bord.storages.s3.S3Storage",
    "GENERATOR_CLASS": "bord.generators.default.DefaultPathGenerator",
    "BUCKET": "parquet",
}


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "elements.wsgi.application"
ASGI_APPLICATION = "elements.asgi.application"

# Database
# https://docs.djangoproject.com/en/3.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": conf.postgres.db_name,
        "USER": conf.postgres.user,
        "PASSWORD": conf.postgres.password,
        "HOST": conf.postgres.host,
        "PORT": conf.postgres.port,
    }
}

CHANNEL_LAYERS = {
    "default": {
        # This example app uses the Redis channel layer implementation channels_redis
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [(conf.redis.host, conf.redis.port)],
        },
    },
}

AUTH_USER_MODEL = "lok.LokUser"
AUTHENTICATION_BACKENDS = (
    "django.contrib.auth.backends.ModelBackend",
    "guardian.backends.ObjectPermissionBackend",
)

# Password validation
# https://docs.djangoproject.com/en/3.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

GRAPHENE = {"SCHEMA": "balder.schema.graphql_schema"}

# Internationalization
# https://docs.djangoproject.com/en/3.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.1/howto/static-files/

STATIC_URL = "/static/"
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static"),
]


REDIS_URL = f"redis://{conf.redis.host}:{conf.redis.port}"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "()": "colorlog.ColoredFormatter",  # colored output
            # exact format is not important, this is the minimum information
            "format": "%(log_color)s[%(levelname)s]  %(name)s %(asctime)s :: %(message)s",
            "log_colors": {
                "DEBUG": "bold_black",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        },
    },
    "handlers": {
        "console": {
            "class": "colorlog.StreamHandler",
            "formatter": "console",
        },
    },
    "loggers": {
        # root logger
        "": {
            "level": "INFO",
            "handlers": ["console"],
        },
        "oauthlib": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": True,
        },
        "delt": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "oauth2_provider": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
