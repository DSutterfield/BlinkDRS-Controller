"""Bounded FP32 detail inference; original model remains the fallback."""
import os
import numpy as np
import torch
from torchvision.models.detection.faster_rcnn import TwoMLPHead


def _parameters(head):
    return (head.fc6.weight, head.fc6.bias, head.fc7.weight, head.fc7.bias)


def _signature(head):
    return tuple(None if p is None else (p.data_ptr(), p._version, p.dtype, p.device)
                 for p in _parameters(head))


class Fp32RegionHead(torch.nn.Module):
    """Same two affine/ReLU layers, using the installed single-thread BLAS."""
    def __init__(self, original):
        super().__init__()
        self.original = original
        self.parameter_signature = _signature(original)
        self.arrays = tuple((layer.weight.detach().numpy(),
                             None if layer.bias is None else layer.bias.detach().numpy())
                            for layer in (original.fc6, original.fc7))
        self.eval()

    def forward(self, x):
        if (self.training or torch.is_grad_enabled() or x.device.type != 'cpu'
                or x.dtype != torch.float32
                or _signature(self.original) != self.parameter_signature):
            return self.original(x)
        values = x.flatten(start_dim=1).numpy()
        for weight, bias in self.arrays:
            values = np.matmul(values, weight.T)
            if bias is not None:
                np.add(values, bias, out=values)
            np.maximum(values, 0, out=values)
        return torch.from_numpy(values)


def optimize_detail_detector(detector, environ=None):
    environ = os.environ if environ is None else environ
    enabled = environ.get('BLINK_DETAIL_OPTIMIZED', '1')
    if enabled not in ('0', '1'):
        raise ValueError('BLINK_DETAIL_OPTIMIZED must be 0 or 1')
    if enabled == '0':
        return detector, 'legacy'
    head = detector.roi_heads.box_head
    # Revalidate other runtimes before enabling their numerical kernels.
    if (torch.__version__.split('+')[0] != '2.8.0'
            or type(head) is not TwoMLPHead or detector.training
            or environ.get('OPENBLAS_NUM_THREADS') != '1'
            or any(p is not None and (p.device.type != 'cpu' or p.dtype != torch.float32)
                   for p in _parameters(head))):
        return detector, 'legacy-unsupported'
    replacement = Fp32RegionHead(head)
    detector.to(memory_format=torch.channels_last)
    detector.roi_heads.box_head = replacement
    return detector, 'channels-last-numpy-fp32'
