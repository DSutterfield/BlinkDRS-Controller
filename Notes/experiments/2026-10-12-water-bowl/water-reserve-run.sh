set -e
cd /home/dan/BlinkDRS-Controller
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 15 .yolo-study-20261009/venv/bin/python -u .water-combined-20261012/water-reserve-run.py
systemctl is-active blink-dvr.service blink-identity.service
