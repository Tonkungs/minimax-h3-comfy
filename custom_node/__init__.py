"""MiniMax H3 helper extension for the default I2V workflow."""

from .fast_save import H3FastVideoSave

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {"H3FastVideoSave": H3FastVideoSave}
NODE_DISPLAY_NAME_MAPPINGS = {"H3FastVideoSave": "H3 Fast Video Save"}
__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
