from .base import BaseCondition, Condition
from .model import UserSegment
from .ruls import UserSegmenter, SimpleCondition
from .manager import SegmentationManager

# Key segmentation
from .key_model import KeySegment
from .key_ruls import KeySegmenter, KeyCondition
from .key_manager import KeySegmentationManager

__all__ = [
    # User segmentation
    "BaseCondition",
    "Condition",
    "UserSegment",
    "UserSegmenter",
    "SimpleCondition",
    "SegmentationManager",
    # Key segmentation
    "KeySegment",
    "KeySegmenter",
    "KeyCondition",
    "KeySegmentationManager",
]
