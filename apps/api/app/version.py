"""Product version and validation posture.

1.1.0 wires the Frankenstein runtimes. Local integration tests can pass
without a GPU. Real Qwen quality is not claimed until a GPU rerun.
"""

__version__ = "1.1.0"
VALIDATION_STATUS = "FRANKENSTEIN_LOCAL_INTEGRATION_GPU_PENDING"
GPU_VALIDATED = False
