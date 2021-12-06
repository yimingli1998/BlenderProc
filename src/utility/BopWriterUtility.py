import json
import os
import math
import glob
import numpy as np
import png
import shutil

import bpy
from mathutils import Euler, Matrix, Vector

from src.utility.BlenderUtility import get_all_blender_mesh_objects, load_image
from src.utility.Utility import Utility
from src.utility.PostProcessingUtility import PostProcessingUtility
from src.utility.WriterUtility import WriterUtility
from src.writer.CameraStateWriter import CameraStateWriter


class BopWriterUtility:
    """ Saves the synthesized dataset in the BOP format. The dataset is split
        into chunks which are saved as individual "scenes". For more details
        about the BOP format, visit the BOP toolkit docs:
        https://github.com/thodan/bop_toolkit/blob/master/docs/bop_datasets_format.md

    """
        
    @staticmethod
    def _load_json(path, keys_to_int=False):
        """Loads content of a JSON file.
        From the BOP toolkit (https://github.com/thodan/bop_toolkit).

        :param path: Path to the JSON file.
        :param keys_to_int: Convert digit dict keys to integers. Default: False
        :return: Content of the loaded JSON file.
        """
            
        # Keys to integers.
        def convert_keys_to_int(x):
            return {int(k) if k.lstrip('-').isdigit() else k: v for k, v in x.items()}

        with open(path, 'r') as f:
            if keys_to_int:
                content = json.load(f, object_hook=lambda x: convert_keys_to_int(x))
            else:
                content = json.load(f)

        return content

    
    @staticmethod
    def _save_json(path, content):
        """ Saves the content to a JSON file in a human-friendly format.
        From the BOP toolkit (https://github.com/thodan/bop_toolkit).

        :param path: Path to the output JSON file.
        :param content: Dictionary/list to save.
        """
        with open(path, 'w') as f:

            if isinstance(content, dict):
                f.write('{\n')
                content_sorted = sorted(content.items(), key=lambda x: x[0])
                for elem_id, (k, v) in enumerate(content_sorted):
                    f.write(
                        '  \"{}\": {}'.format(k, json.dumps(v, sort_keys=True)))
                    if elem_id != len(content) - 1:
                        f.write(',')
                    f.write('\n')
                f.write('}')

            elif isinstance(content, list):
                f.write('[\n')
                for elem_id, elem in enumerate(content):
                    f.write('  {}'.format(json.dumps(elem, sort_keys=True)))
                    if elem_id != len(content) - 1:
                        f.write(',')
                    f.write('\n')
                f.write(']')

            else:
                json.dump(content, f, sort_keys=True)

    
    @staticmethod
    def _save_depth(path, im):
        """Saves a depth image (16-bit) to a PNG file.
        From the BOP toolkit (https://github.com/thodan/bop_toolkit).

        :param path: Path to the output depth image file.
        :param im: ndarray with the depth image to save.
        """
        if not path.endswith(".png"):
            raise ValueError('Only PNG format is currently supported.')

        im[im > 65535] = 65535
        im_uint16 = np.round(im).astype(np.uint16)

        # PyPNG library can save 16-bit PNG and is faster than imageio.imwrite().
        w_depth = png.Writer(im.shape[1], im.shape[0], greyscale=True, bitdepth=16)
        with open(path, 'wb') as f:
            w_depth.write(f, np.reshape(im_uint16, (-1, im.shape[1])))



    
    @staticmethod
    def write(output_dir:str, dataset:str="", append_to_existing_output:bool=False, depth_scale:float=1.0, 
              save_world2cam:bool=True, ignore_dist_thres:float=100., m2mm:bool=True, frames_per_chunk:int=1000):
        """Write the BOP data

        :param output_dir: Path to the output directory.
        :param dataset: Only save annotations for objects of the specified bop dataset. Saves all object poses if undefined.
        :param append_to_existing_output: If true, the new frames will be appended to the existing ones.
        :param depth_scale: Multiply the uint16 output depth image with this factor to get depth in mm. Used to trade-off between depth accuracy 
            and maximum depth value. Default corresponds to 65.54m maximum depth and 1mm accuracy.
        :param save_world2cam: If true, camera to world transformations "cam_R_w2c", "cam_t_w2c" are saved in scene_camera.json
        :param ignore_dist_thres: Distance between camera and object after which object is ignored. Mostly due to failed physics.
        :param m2mm: Original bop annotations and models are in mm. If true, we convert the gt annotations to mm here. This
            is needed if BopLoader option mm2m is used.
        :param frames_per_chunk: Number of frames saved in each chunk (called scene in BOP) 
        """
        
        # Output paths.
        dataset_dir = os.path.join(output_dir, 'bop_data', dataset)
        chunks_dir = os.path.join(dataset_dir, 'train_pbr')
        camera_path = os.path.join(dataset_dir, 'camera.json')

        # Create the output directory structure.
        if not os.path.exists(dataset_dir):
            os.makedirs(dataset_dir)
            os.makedirs(chunks_dir)
        elif not append_to_existing_output:
            raise Exception("The output folder already exists: {}.".format(dataset_dir))
        
        all_mesh_objects = get_all_blender_mesh_objects()
	
        # Select objects from the specified dataset.
        if dataset:
            dataset_objects = []
            for obj in all_mesh_objects:
                if "bop_dataset_name" in obj:
                    if obj["bop_dataset_name"] == dataset:
                        dataset_objects.append(obj)
        else:
            dataset_objects = all_mesh_objects

        # Check if there is any object from the specified dataset.
        if not dataset_objects:
            raise Exception("The scene does not contain any object from the "
                            "specified dataset: {}. Either remove the dataset parameter "
                            "or assign custom property 'bop_dataset_name' to selected objects".format(dataset))
        
        # Save the data.
        BopWriterUtility._write_camera(camera_path, depth_scale=depth_scale)
        BopWriterUtility._write_frames(chunks_dir, dataset_objects=dataset_objects, frames_per_chunk=frames_per_chunk, 
                           m2mm=m2mm, ignore_dist_thres=ignore_dist_thres, save_world2cam=save_world2cam)
    
    @staticmethod
    def _write_camera(camera_path, depth_scale = 0.1):
        """ Writes camera.json into dataset_dir.
        """

        cam_K = WriterUtility.get_cam_attribute(bpy.context.scene.camera, 'cam_K')
        camera = {'cx': cam_K[0][2],
                  'cy': cam_K[1][2],
                  'depth_scale': depth_scale,
                  'fx': cam_K[0][0],
                  'fy': cam_K[1][1],
                  'height': bpy.context.scene.render.resolution_y,
                  'width': bpy.context.scene.render.resolution_x}

        BopWriterUtility._save_json(camera_path, camera)
    
    @staticmethod
    def _get_frame_gt(dataset_objects, unit_scaling, ignore_dist_thres, destination_frame = ["X", "-Y", "-Z"]):
        """ Returns GT pose annotations between active camera and objects.
        
        :return: A list of GT annotations.
        """
        
        H_c2w_opencv = Matrix(WriterUtility.get_cam_attribute(bpy.context.scene.camera, 'cam2world_matrix', destination_frame))
        
        frame_gt = []
        for obj in dataset_objects:
            
            H_m2w = Matrix(WriterUtility.get_common_attribute(obj, 'matrix_world'))

            cam_H_m2c = H_c2w_opencv.inverted() @ H_m2w
            cam_R_m2c = cam_H_m2c.to_quaternion().to_matrix()
            cam_t_m2c = cam_H_m2c.to_translation()

            # ignore examples that fell through the plane
            if not np.linalg.norm(list(cam_t_m2c)) > ignore_dist_thres:
                cam_t_m2c = list(cam_t_m2c * unit_scaling)
                frame_gt.append({
                    'cam_R_m2c': list(cam_R_m2c[0]) + list(cam_R_m2c[1]) + list(cam_R_m2c[2]),
                    'cam_t_m2c': cam_t_m2c,
                    'obj_id': obj["category_id"]
                })
            else:
                print('ignored obj, ', obj["category_id"], 'because either ')
                print('(1) it is further away than parameter "ignore_dist_thres: ",', ignore_dist_thres) 
                print('(e.g. because it fell through a plane during physics sim)')
                print('or')
                print('(2) the object pose has not been given in meters')
                
        return frame_gt
        
    @staticmethod
    def _get_frame_camera(save_world2cam, depth_scale=1.0, unit_scaling=1000., destination_frame = ["X", "-Y", "-Z"]):
        """ Returns camera parameters for the active camera.
        
        :return: dict containing info for scene_camera.json 
        """
        
        cam_K = WriterUtility.get_cam_attribute(bpy.context.scene.camera, 'cam_K', destination_frame)
        
        frame_camera_dict = {
            'cam_K': cam_K[0] + cam_K[1] + cam_K[2],
            'depth_scale': depth_scale
        }
        
        if save_world2cam:
            H_c2w_opencv = Matrix(WriterUtility.get_cam_attribute(bpy.context.scene.camera, 'cam2world_matrix', destination_frame))
            
            H_w2c_opencv = H_c2w_opencv.inverted()
            R_w2c_opencv = H_w2c_opencv.to_quaternion().to_matrix()
            t_w2c_opencv = H_w2c_opencv.to_translation() * unit_scaling
            
            frame_camera_dict['cam_R_w2c'] = list(R_w2c_opencv[0]) + list(R_w2c_opencv[1]) + list(R_w2c_opencv[2])
            frame_camera_dict['cam_t_w2c'] = list(t_w2c_opencv)
        
        return frame_camera_dict
    
    @staticmethod
    def _write_frames(chunks_dir, dataset_objects, depth_scale:float=1.0, frames_per_chunk:int=1000, m2mm:bool=True, 
                            ignore_dist_thres:float=100., save_world2cam:bool=True):
        """ Writes images, GT annotations and camera info.
        """
        
        # Format of the depth images.
        depth_ext = '.png'
        
        rgb_tpath = os.path.join(chunks_dir, '{chunk_id:06d}', 'rgb', '{im_id:06d}' + '{im_type}')
        depth_tpath = os.path.join(chunks_dir, '{chunk_id:06d}', 'depth', '{im_id:06d}' + depth_ext)
        chunk_camera_tpath = os.path.join(chunks_dir, '{chunk_id:06d}', 'scene_camera.json')
        chunk_gt_tpath = os.path.join(chunks_dir, '{chunk_id:06d}', 'scene_gt.json')
        
        # Paths to the already existing chunk folders (such folders may exist
        # when appending to an existing dataset).
        chunk_dirs = sorted(glob.glob(os.path.join(chunks_dir, '*')))
        chunk_dirs = [d for d in chunk_dirs if os.path.isdir(d)]

        # Get ID's of the last already existing chunk and frame.
        curr_chunk_id = 0
        curr_frame_id = 0
        if len(chunk_dirs):
            last_chunk_dir = sorted(chunk_dirs)[-1]
            last_chunk_gt_fpath = os.path.join(last_chunk_dir, 'scene_gt.json')
            chunk_gt = BopWriterUtility._load_json(last_chunk_gt_fpath, keys_to_int=True)

            # Last chunk and frame ID's.
            last_chunk_id = int(os.path.basename(last_chunk_dir))
            last_frame_id = int(sorted(chunk_gt.keys())[-1])

            # Current chunk and frame ID's.
            curr_chunk_id = last_chunk_id
            curr_frame_id = last_frame_id + 1
            if curr_frame_id % frames_per_chunk == 0:
                curr_chunk_id += 1
                curr_frame_id = 0

        # Initialize structures for the GT annotations and camera info.
        chunk_gt = {}
        chunk_camera = {}
        if curr_frame_id != 0:
            # Load GT and camera info of the chunk we are appending to.
            chunk_gt = BopWriterUtility._load_json(
                chunk_gt_tpath.format(chunk_id=curr_chunk_id), keys_to_int=True)
            chunk_camera = BopWriterUtility._load_json(
                chunk_camera_tpath.format(chunk_id=curr_chunk_id), keys_to_int=True)

        # Go through all frames.
        num_new_frames = bpy.context.scene.frame_end - bpy.context.scene.frame_start
        for frame_id in range(bpy.context.scene.frame_start, bpy.context.scene.frame_end):
            # Activate frame.
            bpy.context.scene.frame_set(frame_id)

            # Reset data structures and prepare folders for a new chunk.
            if curr_frame_id == 0:
                chunk_gt = {}
                chunk_camera = {}
                os.makedirs(os.path.dirname(
                    rgb_tpath.format(chunk_id=curr_chunk_id, im_id=0, im_type='PNG')))
                os.makedirs(os.path.dirname(
                    depth_tpath.format(chunk_id=curr_chunk_id, im_id=0)))

            # Get GT annotations and camera info for the current frame.
            
            # Output translation gt in m or mm
            unit_scaling = 1000. if m2mm else 1.
            
            chunk_gt[curr_frame_id] = BopWriterUtility._get_frame_gt(dataset_objects, unit_scaling, ignore_dist_thres)
            chunk_camera[curr_frame_id] = BopWriterUtility._get_frame_camera(save_world2cam, depth_scale, unit_scaling)

            # Copy the resulting RGB image.
            rgb_output = Utility.find_registered_output_by_key("colors")
            if rgb_output is None:
                raise Exception("RGB image has not been rendered.")
            image_type = '.png' if rgb_output['path'].endswith('png') else '.jpg'
            rgb_fpath = rgb_tpath.format(chunk_id=curr_chunk_id, im_id=curr_frame_id, im_type=image_type)
            shutil.copyfile(rgb_output['path'] % frame_id, rgb_fpath)

            # Load the resulting dist image.
            # dist_output = Utility.find_registered_output_by_key("distance")
            # if dist_output is None:
            #     raise Exception("Distance image has not been rendered.")
            # distance = WriterUtility.load_output_file(Utility.resolve_path(dist_output['path'] % frame_id))
            # depth = PostProcessingUtility.dist2depth(distance)
            depth_output = Utility.find_registered_output_by_key("depth")
            if depth_output is None:
                raise Exception("Depth image has not been rendered.")
            depth = WriterUtility.load_output_file(Utility.resolve_path(depth_output['path'] % frame_id))
            depth = PostProcessingUtility.trim_redundant_channels(depth)

            # Scale the depth to retain a higher precision (the depth is saved
            # as a 16-bit PNG image with range 0-65535).
            depth_mm = 1000.0 * depth  # [m] -> [mm]
            depth_mm_scaled = depth_mm / float(depth_scale)

            # Save the scaled depth image.
            depth_fpath = depth_tpath.format(chunk_id=curr_chunk_id, im_id=curr_frame_id)
            BopWriterUtility._save_depth(depth_fpath, depth_mm_scaled)

            # Save the chunk info if we are at the end of a chunk or at the last new frame.
            if ((curr_frame_id + 1) % frames_per_chunk == 0) or\
                  (frame_id == num_new_frames - 1):

                # Save GT annotations.
                BopWriterUtility._save_json(chunk_gt_tpath.format(chunk_id=curr_chunk_id), chunk_gt)

                # Save camera info.
                BopWriterUtility._save_json(chunk_camera_tpath.format(chunk_id=curr_chunk_id), chunk_camera)

                # Update ID's.
                curr_chunk_id += 1
                curr_frame_id = 0
            else:
                curr_frame_id += 1
