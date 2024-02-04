import os
import os.path as osp
import torch
import torch.nn as nn
import torch.optim as optim
import cv2
import numpy as np
import colorsys
import json
import argparse

import __init_path
import models
from core.config import cfg
from aug_utils import j2d_processing
from coord_utils import get_bbox, process_bbox
from funcs_utils import load_checkpoint, save_obj
from graph_utils import build_coarse_graphs
# from renderer import Renderer
from vis import vis_2d_keypoints, vis_coco_skeleton
from _mano import MANO
from smpl import SMPL

def convert_crop_cam_to_orig_img(cam, bbox, img_width, img_height):
    '''
    Convert predicted camera from cropped image coordinates
    to original image coordinates
    :param cam (ndarray, shape=(3,)): weak perspective camera in cropped img coordinates
    :param bbox (ndarray, shape=(4,)): bbox coordinates (c_x, c_y, h)
    :param img_width (int): original image width
    :param img_height (int): original image height
    :return:
    '''
    x, y, w, h = bbox[:,0], bbox[:,1], bbox[:,2], bbox[:, 3]
    cx, cy, h = x + w/2, y + h/2, h
    # cx, cy, h = bbox[:,0], bbox[:,1], bbox[:,2]
    hw, hh = img_width / 2., img_height / 2.
    sx = cam[:,0] * (1. / (img_width / h))
    sy = cam[:,0] * (1. / (img_height / h))
    tx = ((cx - hw) / hw / sx) + cam[:,1]
    ty = ((cy - hh) / hh / sy) + cam[:,2]
    orig_cam = np.stack([sx, sy, tx, ty]).T
    return orig_cam


# def render(result, orig_height, orig_width, orig_img, mesh_face, color):
#     pred_verts, pred_cam, bbox = result['mesh'], result['cam_param'][None, :], result['bbox'][None, :]

#     orig_cam = convert_crop_cam_to_orig_img(
#         cam=pred_cam,
#         bbox=bbox,
#         img_width=orig_width,
#         img_height=orig_height
#     )

#     # Setup renderer for visualization
#     renderer = Renderer(mesh_face, resolution=(orig_width, orig_height), orig_img=True, wireframe=False)
#     renederd_img = renderer.render(
#         orig_img,
#         pred_verts,
#         cam=orig_cam[0],
#         color=color,
#         mesh_filename=None,
#         rotate=False
#     )

#     return renederd_img


def get_joint_setting(mesh_model, joint_category='human36'):
    joint_regressor, joint_num, skeleton, graph_L, graph_perm_reverse = None, None, None, None, None
    if joint_category == 'human36':
        joint_regressor = mesh_model.joint_regressor_h36m
        joint_num = 17
        skeleton = (
        (0, 7), (7, 8), (8, 9), (9, 10), (8, 11), (11, 12), (12, 13), (8, 14), (14, 15), (15, 16), (0, 1), (1, 2),
        (2, 3), (0, 4), (4, 5), (5, 6))
        flip_pairs = ((1, 4), (2, 5), (3, 6), (14, 11), (15, 12), (16, 13))
        graph_Adj, graph_L, graph_perm,graph_perm_reverse = \
            build_coarse_graphs(mesh_model.face, joint_num, skeleton, flip_pairs, levels=9)
        model_chk_path = './experiment/pose2mesh_human36J_train_human36/final.pth.tar'

    elif joint_category == 'coco':
        joint_regressor = mesh_model.joint_regressor_coco
        joint_num = 19  # add pelvis and neck
        skeleton = (
            (1, 2), (0, 1), (0, 2), (2, 4), (1, 3), (6, 8), (8, 10), (5, 7), (7, 9), (12, 14), (14, 16), (11, 13),
            (13, 15),  # (5, 6), #(11, 12),
            (17, 11), (17, 12), (17, 18), (18, 5), (18, 6), (18, 0))
        flip_pairs = ((1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16))
        graph_Adj, graph_L, graph_perm, graph_perm_reverse = \
            build_coarse_graphs(mesh_model.face, joint_num, skeleton, flip_pairs, levels=9)
        model_chk_path = './experiment/pose2mesh_cocoJ_train_human36_coco_muco/final.pth.tar'

    elif joint_category == 'smpl':
        joint_regressor = mesh_model.layer['neutral'].th_J_regressor.numpy().astype(np.float32)
        joint_num = 24
        skeleton = (
            (0, 1), (1, 4), (4, 7), (7, 10), (0, 2), (2, 5), (5, 8), (8, 11), (0, 3), (3, 6), (6, 9), (9, 14), (14, 17),
            (17, 19), (19, 21), (21, 23), (9, 13), (13, 16), (16, 18), (18, 20), (20, 22), (9, 12), (12, 15))
        flip_pairs = ((1, 2), (4, 5), (7, 8), (10, 11), (13, 14), (16, 17), (18, 19), (20, 21), (22, 23))
        graph_Adj, graph_L, graph_perm, graph_perm_reverse = \
            build_coarse_graphs(mesh_model.face, joint_num, skeleton, flip_pairs, levels=9)
        model_chk_path = './experiment/pose2mesh_smplJ_train_surreal/final.pth.tar'

    elif joint_category == 'mano':
        joint_regressor = mesh_model.joint_regressor
        joint_num = 21
        skeleton = ( (0,1), (0,5), (0,9), (0,13), (0,17), (1,2), (2,3), (3,4), (5,6), (6,7), (7,8), (9,10), (10,11), (11,12), (13,14), (14,15), (15,16), (17,18), (18,19), (19,20) )
        hori_conn = (
        (1, 5), (5, 9), (9, 13), (13, 17), (2, 6), (6, 10), (10, 14), (14, 18), (3, 7), (7, 11), (11, 15), (15, 19),
        (4, 8), (8, 12), (12, 16), (16, 20))
        graph_Adj, graph_L, graph_perm, graph_perm_reverse = \
            build_coarse_graphs(mesh_model.face, joint_num, skeleton, hori_conn, levels=6)
        model_chk_path = './experiment/pose2mesh_manoJ_train_freihand/final.pth.tar'

    else:
        raise NotImplementedError(f"{joint_category}: unknown joint set category")

    model = models.pose2mesh_net.get_model(joint_num, graph_L)
    checkpoint = load_checkpoint(load_dir=model_chk_path)
    model.load_state_dict(checkpoint['model_state_dict'])

    return model, joint_regressor, joint_num, skeleton, graph_L, graph_perm_reverse


def add_pelvis(joint_coord, joints_name):
    lhip_idx = joints_name.index('L_Hip')
    rhip_idx = joints_name.index('R_Hip')
    pelvis = (joint_coord[lhip_idx, :] + joint_coord[rhip_idx, :]) * 0.5
    pelvis[2] = joint_coord[lhip_idx, 2] * joint_coord[rhip_idx, 2]  # confidence for pelvis
    pelvis = pelvis.reshape(1, 3)

    joint_coord = np.concatenate((joint_coord, pelvis))
    return joint_coord


def add_neck(joint_coord, joints_name):
    lshoulder_idx = joints_name.index('L_Shoulder')
    rshoulder_idx = joints_name.index('R_Shoulder')
    neck = (joint_coord[lshoulder_idx, :] + joint_coord[rshoulder_idx, :]) * 0.5
    neck[2] = joint_coord[lshoulder_idx, 2] * joint_coord[rshoulder_idx, 2]  # confidence for neck
    neck = neck.reshape(1,3)

    joint_coord = np.concatenate((joint_coord, neck))
    return joint_coord


def optimize_cam_param(project_net, joint_input, crop_size):
    bbox = get_bbox(joint_input)
    bbox1 = process_bbox(bbox.copy(), aspect_ratio=1.0, scale=1.25)
    bbox2 = process_bbox(bbox.copy())
    proj_target_joint_img, trans = j2d_processing(joint_input.copy(), (crop_size, crop_size), bbox1, 0, 0, None)
    joint_img, _ = j2d_processing(joint_input.copy(), (cfg.MODEL.input_shape[1], cfg.MODEL.input_shape[0]), bbox2, 0, 0, None)

    joint_img = joint_img[:, :2]
    joint_img /= np.array([[cfg.MODEL.input_shape[1], cfg.MODEL.input_shape[0]]])
    mean, std = np.mean(joint_img, axis=0), np.std(joint_img, axis=0)
    joint_img = (joint_img.copy() - mean) / std
    joint_img = torch.Tensor(joint_img[None, :, :]).cuda()
    target_joint = torch.Tensor(proj_target_joint_img[None, :, :2]).cuda()

    # get optimization settings for projection
    criterion = nn.L1Loss()
    optimizer = optim.Adam(project_net.parameters(), lr=0.1)

    # estimate mesh, pose
    model.eval()
    pred_mesh, _ = model(joint_img)
    pred_mesh = pred_mesh[:, graph_perm_reverse[:mesh_model.face.max() + 1], :]
    pred_3d_joint = torch.matmul(joint_regressor, pred_mesh)

    out = {}
    # assume batch=1
    project_net.train()
    for j in range(0, 1500):
        # projection
        pred_2d_joint = project_net(pred_3d_joint.detach())

        loss = criterion(pred_2d_joint, target_joint[:, :17, :])
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if j == 500:
            for param_group in optimizer.param_groups:
                param_group['lr'] = 0.05
        if j == 1000:
            for param_group in optimizer.param_groups:
                param_group['lr'] = 0.001

    out['mesh'] = pred_mesh[0].detach().cpu().numpy()
    out['cam_param'] = project_net.cam_param[0].detach().cpu().numpy()
    out['bbox'] = bbox1
    out['prediction_3d'] = pred_3d_joint
    out['target'] = proj_target_joint_img

    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Render Pose2Mesh output')
    parser.add_argument('--gpu', type=str, default='0', help='assign gpu number')
    parser.add_argument('--input_dir', type=str, default='demo/input_frames', help='path of input 2D pose')
    parser.add_argument('--input_img', type=str, default='.', help='path of input image')
    parser.add_argument('--joint_set', type=str, default='coco', help='choose the topology of input 2D pose from [human36, coco, smpl, mano]')
    parser.add_argument('--prefix', type=str, default='frame', help='use prefix to split up files and organize by number')

    args = parser.parse_args()
    os.environ['CUDA_VISIBLE_DEVICES'] = str(args.gpu)
    virtual_crop_size = 500
    joint_set = args.joint_set
    input_dir = args.input_dir
    prefix = args.prefix
    output_path = './demo/result/'
    cfg.DATASET.target_joint_set = joint_set
    cfg.MODEL.posenet_pretrained = False

    # prepare model
    if joint_set == 'mano':
        mesh_model = MANO()
    else:
        mesh_model = SMPL()
    model, joint_regressor, joint_num, skeleton, graph_L, graph_perm_reverse = get_joint_setting(mesh_model, joint_category=joint_set)
    model = model.cuda()
    joint_regressor = torch.Tensor(joint_regressor).cuda()

    if input_dir != '.':  # user specific input
        # get camera parameters
        project_net = models.project_net.get_model(crop_size=virtual_crop_size).cuda()

        fileindices = []
        input_paths = []

        for input_path in os.listdir(input_dir):
            if("npy" in osp.splitext(input_path)[1]):
                fileindices.append(int(osp.splitext(input_path)[0].split(prefix)[-1]))
                input_paths.append(input_path)

        fileindices = np.array(fileindices)
        input_paths = input_paths[np.argsort(fileindices)]

        print(fileindices)

        count = 0
        file_count = len([name for name in os.listdir(input_dir) if "npy" in osp.splitext(name)[1]])

        for input_path in input_paths:

            if count > 100:

                break

            if("npy" in osp.splitext(input_path)[1]):

                print(str(int(count/file_count * 1000)/1000*100) + "%")
                
                joint_input = np.load(input_dir + "/" + input_path)

                if args.input_img != '.':
                    orig_img = cv2.imread(input_path)
                    orig_width, orig_height = orig_img.shape[:2]
                else:
                    orig_width, orig_height = int(np.max(joint_input[:, 0]) * 1.5), int(np.max(joint_input[:, 1]) * 1.5)
                    orig_img = np.zeros((orig_height, orig_width,3))

                out = optimize_cam_param(project_net, joint_input, crop_size=virtual_crop_size)

                # vis mesh
                # color = colorsys.hsv_to_rgb(np.random.rand(), 0.5, 1.0)
                # rendered_img = render(out, orig_height, orig_width, orig_img, mesh_model.face, color)  # s[idx])
                # cv2.imwrite(output_path + f'demo_mesh.png', rendered_img)

                # vis 2d pose
                tmpkps = np.zeros((3, len(joint_input)))
                tmpkps[0, :], tmpkps[1, :], tmpkps[2, :] = joint_input[:, 0], joint_input[:, 1], 1
                tmpimg = orig_img.copy().astype(np.uint8)
                pose_vis_img = vis_2d_keypoints(tmpimg, tmpkps, skeleton)
                cv2.imwrite(output_path + f'demo_pose2d.png', pose_vis_img)

                prediction_3d = out['prediction_3d'].cpu()

                save_obj(out['mesh'], mesh_model.face, output_path + osp.splitext(input_path)[0] + '-mesh.obj')
                np.save(output_path + osp.splitext(input_path)[0] + '-position.npy', prediction_3d.detach().numpy())

                count = count + 1
    else:
        print("--input_dir MUST EXIST")