"""camera_tf_trt.py

This is a Camera TensorFlow/TensorRT Object Detection sample code for
Jetson TX2 or TX1.  This script captures and displays video from either
a video file, an image file, an IP CAM, a USB webcam, or the Tegra
onboard camera, and do real-time object detection with example TensorRT
optimized SSD models in NVIDIA's 'tf_trt_models' repository.  Refer to
README.md inside this repository for more information.

This code is written and maintained by JK Jung <jkjung13@gmail.com>.
"""


import sys
import time
import logging
import argparse
import numpy as np
import cv2
import tensorflow as tf
try:
    import tensorflow.contrib.tensorrt as trt
    do_trt = 1
except:
    do_trt = 0
    print("Tensor RT not present")

import os
import datetime

# Append the object detection path to be visible by the program
sys.path.append("./third_party/models/research/")

from utils.camera import Camera
from utils.od_utils import read_label_map, build_trt_pb, load_trt_pb, \
                           write_graph_tensorboard, detect
from utils.visualization import BBoxVisualization
from tf_trt_models.detection import MODELS


# Constants
DEFAULT_MODEL = 'ssd_inception_v2_coco'
DEFAULT_LABELMAP = 'third_party/models/research/object_detection/' \
                   'data/mscoco_label_map.pbtxt'
WINDOW_NAME = 'CameraTFTRTDemo'
BBOX_COLOR = (0, 255, 0)  # green

def parse_args():
    """Parse input arguments."""
    desc = ('This script captures and displays live camera video, '
            'and does real-time object detection with TF-TRT model '
            'on Jetson TX2/TX1')
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument('--file', dest='use_file',
                        help='use a video file as input (remember to '
                        'also set --filename)',
                        action='store_true')
    parser.add_argument('--image', dest='use_image',
                        help='use an image file as input (remember to '
                        'also set --filename)',
                        action='store_true')
    parser.add_argument('--filename', dest='filename',
                        help='video file name, e.g. test.mp4',
                        default=None, type=str)
    parser.add_argument('--rtsp', dest='use_rtsp',
                        help='use IP CAM (remember to also set --uri)',
                        action='store_true')
    parser.add_argument('--uri', dest='rtsp_uri',
                        help='RTSP URI, e.g. rtsp://192.168.1.64:554',
                        default=None, type=str)
    parser.add_argument('--latency', dest='rtsp_latency',
                        help='latency in ms for RTSP [200]',
                        default=200, type=int)
    parser.add_argument('--usb', dest='use_usb',
                        help='use USB webcam (remember to also set --vid)',
                        action='store_true')
    parser.add_argument('--vid', dest='video_dev',
                        help='device # of USB webcam (/dev/video?) [1]',
                        default=1, type=int)
    parser.add_argument('--width', dest='image_width',
                        help='image width [1280]',
                        default=1280, type=int)
    parser.add_argument('--height', dest='image_height',
                        help='image height [720]',
                        default=720, type=int)
    parser.add_argument('--model', dest='model',
                        help='tf-trt object detecion model '
                        '[{}]'.format(DEFAULT_MODEL),
                        default=DEFAULT_MODEL, type=str)
    parser.add_argument('--build', dest='do_build',
                        help='re-build TRT pb file (instead of using'
                        'the previously built version)',
                        action='store_true')
    parser.add_argument('--tensorboard', dest='do_tensorboard',
                        help='write optimized graph summary to TensorBoard',
                        action='store_true')
    parser.add_argument('--labelmap', dest='labelmap_file',
                        help='[{}]'.format(DEFAULT_LABELMAP),
                        default=DEFAULT_LABELMAP, type=str)
    parser.add_argument('--num-classes', dest='num_classes',
                        help='(deprecated and not used) number of object '
                        'classes', type=int)
    parser.add_argument('--confidence', dest='conf_th',
                        help='confidence threshold [0.3]',
                        default=0.3, type=float)
    parser.add_argument('--save', dest='do_save',
                        help='save the processed file into path/to/model'
                        '/filename + model_name.mp4',
                        action='store_true')
    args = parser.parse_args()
    return args


def open_display_window(width, height):
    """Open the cv2 window for displaying images with bounding boxeses."""
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, width, height)
    cv2.moveWindow(WINDOW_NAME, 0, 0)
    cv2.setWindowTitle(WINDOW_NAME, 'Camera TFTRT Object Detection Demo '
                                    'for Jetson TX2/TX1')


def draw_help_and_fps(img, fps):
    """Draw help message and fps number at top-left corner of the image."""
    help_text = "'Esc' to Quit, 'H' for FPS & Help, 'F' for Fullscreen"
    font = cv2.FONT_HERSHEY_PLAIN
    line = cv2.LINE_AA

    fps_text = 'FPS: {:.1f}'.format(fps)
    cv2.putText(img, help_text, (11, 20), font, 3.0, (32, 32, 32), 4, line)
    cv2.putText(img, help_text, (10, 20), font, 3.0, (240, 240, 240), 1, line)
    cv2.putText(img, fps_text, (11, 50), font, 3.0, (32, 32, 32), 4, line)
    cv2.putText(img, fps_text, (10, 50), font, 3.0, (240, 240, 240), 1, line)
    return img


def set_full_screen(full_scrn):
    """Set display window to full screen or not."""
    prop = cv2.WINDOW_FULLSCREEN if full_scrn else cv2.WINDOW_NORMAL
    cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, prop)


def show_bounding_boxes(img, box, conf, cls, cls_dict):
    """Draw detected bounding boxes on the original image."""
    font = cv2.FONT_HERSHEY_DUPLEX
    for bb, cf, cl in zip(box, conf, cls):
        cl = int(cl)
        y_min, x_min, y_max, x_max = bb[0], bb[1], bb[2], bb[3]
        cv2.rectangle(img, (x_min, y_min), (x_max, y_max), BBOX_COLOR, 2)
        txt_loc = (max(x_min, 5), max(y_min-3, 20))
        cls_name = cls_dict.get(cl, 'CLASS{}'.format(cl))
        txt = '{} {:.2f}'.format(cls_name, cf)
        cv2.putText(img, txt, txt_loc, font, 0.8, BBOX_COLOR, 1)
    return img


def loop_and_detect(cam, tf_sess, conf_th, vis, model_name, filename, save_file, od_type):
    """Loop, grab images from camera, and do object detection.

    # Arguments
      cam: the camera object (video source).
      tf_sess: TensorFlow/TensorRT session to run SSD object detection.
      conf_th: confidence/score threshold for object detection.
      vis: for visualization.
    """
    show_fps = True
    full_scrn = False
    fps = 0.0
    tic = time.time()

    _timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H:%M:%S')
    _filename = os.path.splitext(os.path.basename(filename))[0]
    _video_path = './data/{}/{}'.format(MODELS[model_name].extract_dir, _filename + '_' + _timestamp + '.mp4')
    _fourcc = cv2.VideoWriter_fourcc(*'MP4V')

    img = cam.read()
    h, w, _ = img.shape
    if save_file:
        out = cv2.VideoWriter(_video_path, _fourcc, 2.0, (w, h))

    while cam.thread_running:
        if cv2.getWindowProperty(WINDOW_NAME, 0) < 0:
            # Check to see if the user has closed the display window.
            # If yes, terminate the while loop.
            break

        img = cam.read()
        if img is not None:
            box, conf, cls = detect(img, tf_sess, conf_th, od_type=od_type)
            img = vis.draw_bboxes(img, box, conf, cls)
            if show_fps:
                img = draw_help_and_fps(img, fps)
            # Write the frame into the file '_video_name'
            if save_file:
                out.write(img)
            cv2.imshow(WINDOW_NAME, img)
            toc = time.time()
            curr_fps = 1.0 / (toc - tic)
            # calculate an exponentially decaying average of fps number
            fps = curr_fps if fps == 0.0 else (fps*0.9 + curr_fps*0.1)
            tic = toc

        key = cv2.waitKey(1)
        if key == 27:  # ESC key: quit program
            break
        elif key == ord('H') or key == ord('h'):  # Toggle help/fps
            show_fps = not show_fps
        elif key == ord('F') or key == ord('f'):  # Toggle fullscreen
            full_scrn = not full_scrn
            set_full_screen(full_scrn)

        #time.sleep(.3)


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    save_file = 0
    # Ask tensorflow logger not to propagate logs to parent (which causes
    # duplicated logging)
    logging.getLogger('tensorflow').propagate = False

    args = parse_args()
    logger.info('called with args: %s' % args)

    # build the class (index/name) dictionary from labelmap file
    logger.info('reading label map')
    cls_dict = read_label_map(args.labelmap_file)

    if do_trt:
        pb_path = './data/{}_trt.pb'.format(args.model)
    else:
        pb_path = './data/{}/{}'.format(MODELS[args.model].extract_dir, 'frozen_inference_graph.pb')
    log_path = './logs/{}_trt'.format(args.model)

    if args.do_build:
        logger.info('building TRT graph and saving to pb: %s' % pb_path)
        build_trt_pb(args.model, pb_path, args.do_trt)

    if args.do_save:
        save_file = 1

    logger.info('opening camera device/file')
    cam = Camera(args)
    cam.open()
    if not cam.is_opened:
        sys.exit('Failed to open camera!')

    logger.info('loading TRT graph from pb: %s' % pb_path)
    trt_graph = load_trt_pb(pb_path)

    logger.info('starting up TensorFlow session')
    tf_config = tf.ConfigProto()
    tf_config.gpu_options.allow_growth = True
    tf_sess = tf.Session(config=tf_config, graph=trt_graph)

    if args.do_tensorboard:
        logger.info('writing graph summary to TensorBoard')
        write_graph_tensorboard(tf_sess, log_path)

    logger.info('warming up the TRT graph with a dummy image')
    od_type = 'faster_rcnn' if 'faster_rcnn' in args.model else 'ssd'
    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    _, _, _ = detect(dummy_img, tf_sess, conf_th=.3, od_type=od_type)

    cam.start()  # ask the camera to start grabbing images

    # grab image and do object detection (until stopped by user)
    logger.info('starting to loop and detect')
    vis = BBoxVisualization(cls_dict)
    open_display_window(cam.img_width, cam.img_height)
    loop_and_detect(cam, tf_sess, args.conf_th, vis, args.model, args.filename, save_file, od_type=od_type)

    logger.info('cleaning up')
    cam.stop()  # terminate the sub-thread in camera
    tf_sess.close()
    cam.release()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
