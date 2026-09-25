"""Product version and validation posture.

1.0.0 is the intelligence-engine architecture. Local deterministic tests can
pass without a GPU. Real Qwen quality is not claimed until a GPU rerun.
"""

__version__ = "1.0.0"
VALIDATION_STATUS = "ARCHITECTURE_IMPLEMENTED_LOCAL_TESTS_GPU_PENDING"
GPU_VALIDATED = False
