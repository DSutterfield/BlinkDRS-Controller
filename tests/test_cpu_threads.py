import unittest
from unittest.mock import Mock
from identity_models import configure_cpu_threads


class CpuThreadTests(unittest.TestCase):
    def test_default_preserves_single_thread_inference(self):
        torch, cv2 = Mock(), Mock()
        configure_cpu_threads(torch, cv2, {})
        torch.set_num_threads.assert_called_once_with(1)
        torch.set_num_interop_threads.assert_called_once_with(1)
        cv2.setNumThreads.assert_called_once_with(1)

    def test_backends_can_be_tuned_independently(self):
        torch, cv2 = Mock(), Mock()
        configure_cpu_threads(torch, cv2,
                             {'BLINK_AI_THREADS': '2', 'BLINK_AI_CV_THREADS': '3'})
        torch.set_num_threads.assert_called_once_with(2)
        torch.set_num_interop_threads.assert_called_once_with(1)
        cv2.setNumThreads.assert_called_once_with(3)

    def test_bad_configuration_fails_before_changing_either_backend(self):
        for name in ('BLINK_AI_THREADS', 'BLINK_AI_CV_THREADS'):
            for value in ('0', '-1', '4', '2.5', 'many', '', None):
                with self.subTest(name=name, value=value):
                    torch, cv2 = Mock(), Mock()
                    with self.assertRaisesRegex(ValueError, name):
                        configure_cpu_threads(torch, cv2, {name: value})
                    self.assertEqual(torch.mock_calls, [])
                    self.assertEqual(cv2.mock_calls, [])
