"""Prune unused SSD scores while preserving the installed CPU NMS semantics.

Only >.29 scores are relevant: current consumers require >=.30. The fast
path is valid for unique foreground scores and the legacy per-class CPU NMS
backend. Ambiguous, crowded or unsupported inputs use the original method.
"""
class EarlySsdPostprocess:
    def __init__(self, detector, enabled=True):
        import torchvision
        self.detector = detector
        self.original = detector.postprocess_detections
        self.enabled = enabled
        # Re-audit NMS backend selection before enabling on another version.
        self.supported = torchvision.__version__ == '0.23.0'
        self.fast_images = 0
        self.fallback_images = 0

    def __call__(self, head_outputs, image_anchors, image_shapes):
        import torch
        from torchvision.ops import boxes as box_ops
        model = self.detector
        scores = head_outputs['cls_logits'].softmax(dim=-1)
        if not self.enabled or not self.supported or scores.device.type != 'cpu' or model.score_thresh != .001:
            self.fallback_images += len(image_shapes)
            return self.original(head_outputs, image_anchors, image_shapes)
        safe = []
        for score in scores:
            foreground = score[:, 1:]
            # This is exactly torchvision 0.23 CPU batched_nms's backend test:
            # boxes.numel()>4000, after per-class topk_candidates selection.
            original_boxes = int((foreground > model.score_thresh).sum(dim=0).clamp(max=model.topk_candidates).sum())
            high = foreground[foreground > .29]
            safe.append(original_boxes * 4 > 4000
                        and high.numel() < model.detections_per_img
                        and high.unique().numel() == high.numel())
        if not all(safe):
            self.fallback_images += len(image_shapes)
            return self.original(head_outputs, image_anchors, image_shapes)
        results = []
        for regression, score, anchors, shape in zip(head_outputs['bbox_regression'], scores, image_anchors, image_shapes):
            boxes = box_ops.clip_boxes_to_image(model.box_coder.decode_single(regression, anchors), shape)
            image_boxes, image_scores, image_labels = [], [], []
            active = ((score[:, 1:] > .29).any(dim=0).nonzero().flatten() + 1).tolist()
            for label in active:
                keep = score[:, label] > .29
                values, candidates = score[keep, label], boxes[keep]
                values, order = values.topk(min(model.topk_candidates, values.numel()))
                candidates = candidates[order]
                # Use the same per-class local-coordinate NMS as the legacy
                # large candidate set. Never switch to coordinate-offset NMS.
                keep = box_ops.nms(candidates, values, model.nms_thresh)
                image_boxes.append(candidates[keep])
                image_scores.append(values[keep])
                image_labels.append(torch.full_like(values[keep], label, dtype=torch.int64))
            if image_scores:
                values = torch.cat(image_scores)
                order = values.sort(descending=True)[1][:model.detections_per_img]
                result = dict(boxes=torch.cat(image_boxes)[order], scores=values[order], labels=torch.cat(image_labels)[order])
            else:
                result = dict(boxes=boxes.new_empty((0, 4)), scores=score.new_empty((0,)), labels=torch.empty((0,), dtype=torch.int64))
            results.append(result)
        self.fast_images += len(results)
        return results
