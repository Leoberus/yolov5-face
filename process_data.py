# -*- coding: UTF-8 -*-
import argparse
import time
from pathlib import Path
import sys
import os
import math
from IPython.display import Image
import time

import numpy as np
import cv2
import torch
import torch.backends.cudnn as cudnn
from numpy import random
import copy

FILE = Path(__file__).resolve()
ROOT = FILE.parents[0]  # YOLOv5 root directory
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # add ROOT to PATH
ROOT = Path(os.path.relpath(ROOT, Path.cwd()))  # relative

from models.experimental import attempt_load
from utils.datasets import letterbox, img_formats, vid_formats, LoadImages, LoadStreams
from utils.general import check_img_size, non_max_suppression_face, apply_classifier, scale_coords, xyxy2xywh, \
    strip_optimizer, set_logging, increment_path
from utils.plots import plot_one_box
from utils.torch_utils import select_device, load_classifier, time_synchronized

multi_face = []

def load_model(weights, device):
    model = attempt_load(weights, map_location=device)  # load FP32 model
    return model


def scale_coords_landmarks(img1_shape, coords, img0_shape, ratio_pad=None):
    # Rescale coords (xyxy) from img1_shape to img0_shape
    if ratio_pad is None:  # calculate from img0_shape
        gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])  # gain  = old / new
        pad = (img1_shape[1] - img0_shape[1] * gain) / 2, (img1_shape[0] - img0_shape[0] * gain) / 2  # wh padding
    else:
        gain = ratio_pad[0][0]
        pad = ratio_pad[1]

    coords[:, [0, 2, 4, 6, 8]] -= pad[0]  # x padding
    coords[:, [1, 3, 5, 7, 9]] -= pad[1]  # y padding
    coords[:, :10] /= gain
    #clip_coords(coords, img0_shape)
    coords[:, 0].clamp_(0, img0_shape[1])  # x1
    coords[:, 1].clamp_(0, img0_shape[0])  # y1
    coords[:, 2].clamp_(0, img0_shape[1])  # x2
    coords[:, 3].clamp_(0, img0_shape[0])  # y2
    coords[:, 4].clamp_(0, img0_shape[1])  # x3
    coords[:, 5].clamp_(0, img0_shape[0])  # y3
    coords[:, 6].clamp_(0, img0_shape[1])  # x4
    coords[:, 7].clamp_(0, img0_shape[0])  # y4
    coords[:, 8].clamp_(0, img0_shape[1])  # x5
    coords[:, 9].clamp_(0, img0_shape[0])  # y5
    return coords

# Calculate the length of all the edges
def trignometry_for_distance(a, b):
    return math.sqrt(((b[0] - a[0]) * (b[0] - a[0])) \
                     + ((b[1] - a[1]) * (b[1] - a[1])))

def rotate_image(image, angle):
  image_center = tuple(np.array(image.shape[1::-1]) / 2)
  rot_mat = cv2.getRotationMatrix2D(image_center, angle, 1.0)
  result = cv2.warpAffine(image, rot_mat, image.shape[1::-1], flags=cv2.INTER_LINEAR)
  return result

def calculate_rotate(landmarks):
    left_eye_x = int(landmarks[0])
    left_eye_y = int(landmarks[1])

    right_eye_x = int(landmarks[2])
    right_eye_y = int(landmarks[3])

    # finding rotation direction
    if left_eye_y > right_eye_y:
        # print("Rotate image to clock direction")
        point_3rd = (right_eye_x, left_eye_y)
        direction = -1  # rotate image direction to clock
    else:
        # print("Rotate to inverse clock direction")
        point_3rd = (left_eye_x, right_eye_y)
        direction = 1  # rotate inverse direction of clock
    
    a = trignometry_for_distance((left_eye_x,left_eye_y), point_3rd)
    b = trignometry_for_distance((right_eye_x,right_eye_y), point_3rd)
    c = trignometry_for_distance((right_eye_x,right_eye_y), (left_eye_x,left_eye_y))
    cos_a = (b*b + c*c - a*a)/(2*b*c)
    angle = (np.arccos(cos_a) * 180) / math.pi

    if direction == -1:
        angle = 90 - angle
    # else:
    #     angle = -(90-angle)
 

    return angle,direction




def show_results(ori_img,img, xyxy, conf, landmarks, class_num,crop_path):
    h,w,c = img.shape
    tl = 1 or round(0.002 * (h + w) / 2) + 1  # line/font thickness
    x1 = int(xyxy[0])
    y1 = int(xyxy[1])
    x2 = int(xyxy[2])
    y2 = int(xyxy[3])

    img = img.copy()
    img_crop = ori_img.copy()
    
 

    
    cv2.rectangle(img, (x1,y1), (x2, y2), (0,255,0), thickness=tl, lineType=cv2.LINE_AA)
    clors = [(255,0,0),(0,255,0),(0,0,255),(255,255,0),(0,255,255)]

    for i in range(5):
        point_x = int(landmarks[2 * i])
        point_y = int(landmarks[2 * i + 1])
        cv2.circle(img, (point_x, point_y), tl+1, clors[i], -1)

    angle,direction = calculate_rotate(landmarks)

    # cv2.circle(img, (point_x, point_y), tl+1, clors[i], -1)

    img_crop = img_crop[y1:y2 , x1:x2]
    img_crop = rotate_image(img_crop, angle * direction)
    cv2.imwrite(crop_path, img_crop)

    tf = max(tl - 1, 1)  # font thickness
    label = str(conf)[:5]
    cv2.putText(img, label, (x1, y1 - 2), 0, tl / 3, [225, 255, 255], thickness=tf, lineType=cv2.LINE_AA)
    return img


def detect(
    model,
    source,
    device,
    project,
    name,
    exist_ok,
    save_img,
    view_img
):
    # Load model
    img_size = 640
    conf_thres = 0.6
    iou_thres = 0.5
    imgsz=(640, 640)
    
    # Directories
    save_dir = increment_path(Path(project) / name, exist_ok=exist_ok)  # increment run
    Path(save_dir).mkdir(parents=True, exist_ok=True)  # make dir

    is_file = Path(source).suffix[1:] in (img_formats)
    
    bs = 1

    print('loading directory ', source)
    dirs = os.listdir(source) #  '/content/drive/MyDrive/Kaggle/cat_individuals_dataset/'
    for dir in dirs:
        print('loading image ', source+str('/'+dir))
        dataset = LoadImages(source+str('/'+dir), img_size=imgsz)
        bs = 1  # batch_size

        save_dir = increment_path(Path(project) / name / dir, exist_ok=exist_ok)  # increment run
        Path(save_dir).mkdir(parents=True, exist_ok=True)  # make dir

        for path, im, im0s, vid_cap in dataset:
            
            if len(im.shape) == 4:
                orgimg = np.squeeze(im.transpose(0, 2, 3, 1), axis= 0)
            else:
                orgimg = im.transpose(1, 2, 0)
            
            orgimg = cv2.cvtColor(orgimg, cv2.COLOR_BGR2RGB)
            img0 = copy.deepcopy(orgimg)
            h0, w0 = orgimg.shape[:2]  # orig hw
            r = img_size / max(h0, w0)  # resize image to img_size
            if r != 1:  # always resize down, only resize up if training with augmentation
                interp = cv2.INTER_AREA if r < 1  else cv2.INTER_LINEAR
                img0 = cv2.resize(img0, (int(w0 * r), int(h0 * r)), interpolation=interp)

            imgsz = check_img_size(img_size, s=model.stride.max())  # check img_size

            img = letterbox(img0, new_shape=imgsz)[0]
            # Convert from w,h,c to c,w,h
            img = img.transpose(2, 0, 1).copy()

            img = torch.from_numpy(img).to(device)
            img = img.float()  # uint8 to fp16/32
            img /= 255.0  # 0 - 255 to 0.0 - 1.0
            if img.ndimension() == 3:
                img = img.unsqueeze(0)

            # Inference
            pred = model(img)[0]
            
            # Apply NMS
            pred = non_max_suppression_face(pred, conf_thres, iou_thres)
            print(len(pred[0]), 'face' if len(pred[0]) == 1 else 'faces')

            # Process detections
            for i, det in enumerate(pred):  # detections per image
                
                p, im0, frame = path, im0s.copy(), getattr(dataset, 'frame', 0)
                
                p = Path(p)  # to Path
                # save_path = str(Path(save_dir) / p.name)  # im.jpg
                

                if len(det):
                    # Rescale boxes from img_size to im0 size
                    det[:, :4] = scale_coords(img.shape[2:], det[:, :4], im0.shape).round()

                    # Print results
                    for c in det[:, -1].unique():
                        n = (det[:, -1] == c).sum()  # detections per class

                    det[:, 5:15] = scale_coords_landmarks(img.shape[2:], det[:, 5:15], im0.shape).round()

                    ori_img = im0.copy()
                    for j in range(det.size()[0]):  # this loop for each face in image
                        xyxy = det[j, :4].view(-1).tolist()
                        conf = det[j, 4].cpu().numpy()
                        landmarks = det[j, 5:15].view(-1).tolist()
                        class_num = det[j, 15].cpu().numpy()

                        crop_path = str(Path(save_dir) / Path(str(j)+'_'+str(p.name)))  # crop_im.jpg
                        im0 = show_results(ori_img,im0, xyxy, conf, landmarks, class_num,crop_path)
                



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', nargs='+', type=str, default='runs/train/exp5/weights/last.pt', help='model.pt path(s)')
    parser.add_argument('--source', type=str, default='0', help='source')  # file/folder, 0 for webcam
    parser.add_argument('--img-size', type=int, default=224, help='inference size (pixels)')
    parser.add_argument('--project', default=ROOT / 'runs/detect', help='save results to project/name')
    parser.add_argument('--name', default='exp', help='save results to project/name')
    parser.add_argument('--exist-ok', action='store_true', help='existing project/name ok, do not increment')
    parser.add_argument('--save-img', action='store_true', help='save results')
    parser.add_argument('--view-img', action='store_true', help='show results')
    opt = parser.parse_args()
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = load_model(opt.weights, device)
    detect(model, opt.source, device, opt.project, opt.name, opt.exist_ok, opt.save_img, opt.view_img)
