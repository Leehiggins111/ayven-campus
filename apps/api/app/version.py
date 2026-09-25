"""Product version and validation posture.

1.1.1 removes exam-specific research seeds and makes the supervisor,
manager, and RunPod preflight honest. Local tests can pass without a GPU.
Real Qwen quality is not claimed until a GPU rerun.
"""

__version__ = "1.1.1"
VALIDATION_STATUS = "FRANKENSTEIN_LOCAL_INTEGRATION_GPU_PENDING"
GPU_VALIDATED = False
