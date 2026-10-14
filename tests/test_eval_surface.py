import numpy as np
import torch

from backbone.eval_surface import predict_area
from backbone.train_surface import N_CLASSES, confusion, iou_from_confusion


class PixelModel(torch.nn.Module):
    """Per-pixel 'model': class 1 where the normalized red channel is high,
    else class 0. Any tile misplacement in predict_area shows up as shifted
    edges."""

    def forward(self, x):
        logits = torch.full((x.shape[0], N_CLASSES, *x.shape[2:]), -20.0)
        red = x[:, 0] > 0
        logits[:, 1] = torch.where(red, 20.0, -20.0)
        logits[:, 0] = torch.where(red, -20.0, 20.0)
        return logits


def test_predict_area_places_tiles_exactly():
    rng = np.random.default_rng(0)
    h, w = 700, 530  # not multiples of the stride, so the last tiles are shifted
    img = np.zeros((h, w, 3), np.uint8)
    blobs = rng.random((h // 10 + 1, w // 10 + 1)) > 0.6
    truth = np.kron(blobs, np.ones((10, 10), bool))[:h, :w]
    truth[123, 77] = True  # single pixels must survive too
    img[truth, 0] = 255
    prob = predict_area(PixelModel(), img, chip=256, overlap=64, batch=4)
    assert prob.shape == (h, w, N_CLASSES) and prob.dtype == np.uint8
    np.testing.assert_array_equal(prob.argmax(-1) == 1, truth)
    assert prob[..., 1][truth].min() == 255 and prob[..., 1][~truth].max() == 0


def test_iou_from_confusion():
    pred = np.array([0, 0, 1, 1, 2])
    target = np.array([0, 1, 1, 1, 2])
    iou = iou_from_confusion(confusion(pred, target, n=4))
    np.testing.assert_allclose(iou[:3], [1 / 2, 2 / 3, 1.0])
    assert np.isnan(iou[3])  # class absent in both
