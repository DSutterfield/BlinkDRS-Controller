"""Bounded same-frame SSD views; keep their order and pause between pairs."""
from itertools import islice
from PIL import Image


def iter_ssd_views(models, views, checkpoint=None):
    size = getattr(models, 'ssd_batch_size', 1)
    if type(size) is not int or size not in (1, 2):
        raise ValueError('SSD batch size must be 1 or 2')
    views = iter(views)
    while True:
        batch = list(islice(views, size))
        if not batch:
            return
        if checkpoint:
            checkpoint()
        images = [models.detector_transform(Image.fromarray(
            models.cv2.cvtColor(view, models.cv2.COLOR_BGR2RGB)))
            for view, _, _ in batch]
        with models.torch.inference_mode():
            results = models.detector(images)
        if len(results) != len(batch):
            raise RuntimeError('SSD returned an unexpected number of views')
        for (view, x, y), result in zip(batch, results):
            yield view, x, y, result
