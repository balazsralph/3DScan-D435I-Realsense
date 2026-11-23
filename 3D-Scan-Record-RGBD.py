import pyrealsense2 as rs
import numpy as np
import cv2
from os import listdir, makedirs
from os.path import abspath, exists, join
from math import tan, radians
from pathlib import Path
import shutil
import json

def save_intrinsic_as_json(filename, frame):
    intrinsics = frame.profile.as_video_stream_profile().intrinsics
    with open(filename, 'w') as outfile:
        json.dump(
            {
                'width': intrinsics.width,
                'height': intrinsics.height,
                'intrinsic_matrix': [
                    intrinsics.fx, 0, 0,
                    0, intrinsics.fy, 0,
                    intrinsics.ppx, intrinsics.ppy, 1
                ]
            },
            outfile,
            indent=4)

def make_clean_folder(path_folder):
    if exists(path_folder) and listdir(path_folder):
        user_input = input("%s not empty. Overwrite? (y/n) : " % path_folder)
        if user_input.lower() != 'y':
            raise SystemExit(0)
        shutil.rmtree(path_folder)
    makedirs(path_folder, exist_ok=True)

def save_rgbd_pair(path_color, path_depth, frame_count, color_image, depth_image):
    color_file = Path(path_color) / f"{frame_count:06d}.jpg"
    depth_file = Path(path_depth) / f"{frame_count:06d}.png"
    try:
        if not cv2.imwrite(str(depth_file), depth_image):
            raise OSError(f"Could not write {depth_file}")
        if not cv2.imwrite(str(color_file), color_image):
            raise OSError(f"Could not write {color_file}")
    except (cv2.error, OSError, KeyboardInterrupt):
        depth_file.unlink(missing_ok=True)
        color_file.unlink(missing_ok=True)
        raise

def guidelines(input_img):
    # center vertical line (green)
    cv2.line(input_img, (int(width/2), int(0)), (int(width/2), int(height)), (0, 255, 0), 1)
    # left vertical line (white)
    cv2.line(input_img, (int(width/2-height/4), int(0)), (int(width/2-height/4), int(height)), (255, 255, 255), 1)
    # right vertical line (white)
    cv2.line(input_img, (int(width/2+height/4), int(0)), (int(width/2+height/4), int(height)), (255, 255, 255), 1)
    # circle (white)
    cv2.circle(input_img, (int(width/2), int(height/2)), int(height/4), (255, 255, 255), 1)
    # cemter dot (red)
    cv2.circle(input_img, (int(width/2), int(height/2)), 1, (0, 0, 255), 5)
    # hotizontal line (green)
    cv2.line(input_img, (int(0), int(height/2)), (int(width), int(height/2)), (0, 255, 0), 1)
    # outer rectangle (red)
    cv2.rectangle(input_img, (int(width/8), int(height/(8/7))), (int(width/(8/7)), int(height/8)), (0, 0, 255), 1)
    # inner rectangle (green)
    cv2.rectangle(input_img, (int(width/4), int(height/(4/3))), (int(width/(4/3)), int(height/4)), (0, 255, 0), 1)

if __name__ == "__main__":
    
    #create folder
    path_dataset = "dataset"
    path_color = join(path_dataset, "color")
    path_depth = join(path_dataset, "depth")
    make_clean_folder(path_depth)
    make_clean_folder(path_color)

    # create pipeline
    pipeline = rs.pipeline()

    # make realsense configuration
    config = rs.config()

    # import custom preset
    jsonObj = json.load(open("MediumDensityPreset-custom.json"))
    json_string= str(jsonObj).replace("'", '\"')

    # resolution and fps
    width = int(jsonObj['viewer']['stream-width'])
    height = int(jsonObj['viewer']['stream-height'])
    fps = int(jsonObj['viewer']['stream-fps'])
    print("W: ", width)
    print("H: ", height)
    print("FPS: ", fps)

    # recorded frame count from rotation input
    warmup_frames = 30
    frame_loop_at = 506 # includes the initial warm-up frames
    frame_per_rotation = frame_loop_at - warmup_frames
    rotation_period = frame_per_rotation / fps # time (second) needed for 1 rotation
    rpm = 60 / rotation_period
    rotation_count= int(input("Rotation count (int): "))
    if rotation_count < 1:
        raise SystemExit("Rotation count must be at least 1.")
    frame_max = frame_per_rotation * rotation_count
    print("RPM: ", rpm) # rotation per minute
    print(f"Target: {frame_max} RGB-D pairs")
    
    # preparing configuration for streaming
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
    config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)

    # start streaming
    profile = pipeline.start(config)

    # get depth sensors profile
    depth_sensor = profile.get_device()
    depth_first_sensor = profile.get_device().first_depth_sensor()

    # use advanced mode
    advnc_mode = rs.rs400_advanced_mode(depth_sensor)
    advnc_mode.load_json(json_string)

    # set optimal disparity
    depth_table_control_group = advnc_mode.get_depth_table()
    distance = float(input("Center distance from camera (cm): "))
    radius = float(input("Radius from center (cm): "))
    max_range = distance + radius
    print("max_range:", max_range)
    x_res = width
    h_fov = 87
    focal_length = 0.5 * (x_res / (tan(radians(h_fov/2))))
    baseline = 50
    disparity_shift = int(((focal_length * baseline) / max_range) / 10) #divided by 10 for scaling from mm to cm
    print("disparity_shift:", disparity_shift)
    depth_table_control_group.disparityShift = disparity_shift
    advnc_mode.set_depth_table(depth_table_control_group)

    # get depth sensors scale
    depth_scale = depth_first_sensor.get_depth_scale()

    # clip background of object in X meter(s) away
    clip_background_distance = 3 # crop if over 3 meter
    clipping_distance = clip_background_distance / depth_scale

    # align depth frames
    align_to = rs.stream.color
    align = rs.align(align_to)

    frame_count = 0
    recording = False
    print("Camera warming up. Press SPACE in the preview to start recording.")

    try:
        while True:
            # get frameset of color and depth
            frames = pipeline.wait_for_frames()

            # align depth frames to color frames
            aligned_frames = align.process(frames)

            # get aligned frames
            aligned_depth_frame = aligned_frames.get_depth_frame()
            color_frames = aligned_frames.get_color_frame()

            # Validate that both frames are valid
            if not aligned_depth_frame or not color_frames:
                continue

            # Let depth and auto-exposure settle before any images are saved.
            if warmup_frames:
                warmup_frames -= 1
                continue

            # place depth and color image as array
            depth_image = np.asanyarray(aligned_depth_frame.get_data())
            color_image = np.asanyarray(color_frames.get_data())

            if recording:
                if frame_count == 0:
                    save_intrinsic_as_json(join(path_dataset, "camera_intrinsic.json"), color_frames)
                save_rgbd_pair(path_color, path_depth, frame_count, color_image, depth_image)
                frame_count += 1
                if frame_count == frame_max:
                    break
                if frame_count % frame_per_rotation == 0:
                    recording = False
                    print(f"Rotation {frame_count // frame_per_rotation} saved. Press SPACE to resume.")
                
            # color indicator for remove background
            grey_color = 153

            # stack depth and color frame, depth image is 1 channel, color is 3 channels
            depth_image_3d = np.dstack((depth_image, depth_image, depth_image)) # channel

            # remove depth > clipping distance
            bg_removed = np.where((depth_image_3d > clipping_distance) | (depth_image_3d <= 0), grey_color, color_image)

            # apply colormap in depth_images
            depth_colormap = cv2.applyColorMap(cv2.convertScaleAbs(depth_image,alpha=0.09), cv2.COLORMAP_JET)

            # create guidelines for bg_removed and depth_colormap
            guidelines(bg_removed)
            guidelines(depth_colormap)

            # combine bg_removed (left) and depth_colormap (right) in 1 array for window
            images = np.hstack((bg_removed, depth_colormap))

            if recording:
                status = f"RECORDING {frame_count}/{frame_max} | SPACE pause | ESC stop"
            elif frame_count:
                status = f"PAUSED {frame_count}/{frame_max} | SPACE resume | ESC stop"
            else:
                status = "PREVIEW | SPACE start recording | ESC exit"
            images[:30] = 0
            cv2.putText(images, status, (10, 22), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.imshow('Recorder Realsense', images)
            key = cv2.waitKey(1)
            if key == 27:
                break
            if key == 32:
                recording = not recording

    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
        print(f"Saved {frame_count} RGB-D pairs in {abspath(path_dataset)}")
