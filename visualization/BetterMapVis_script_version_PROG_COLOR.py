import pandas as pd
from pathlib import Path
import holoviews as hv
hv.extension('matplotlib')
#from matplotlib.cm import get_cmap
#import matplotlib.pyplot as plt
from PIL import Image
from einops import rearrange
from multiprocessing import Manager, Pool, Process
from tqdm import tqdm
import numpy as np
import tempfile
import cv2

import matplotlib as mpl

def make_all_coords_arrays(filtered_dfs):
    return np.array([tdf[['x', 'y', 'map']].to_numpy().astype(np.uint8) for tdf in filtered_dfs]).transpose(1,0,2)

def get_sprite_by_coords(img, x, y):
    sy = 34+17*y
    sx = 9 +17*x
    alpha_v = np.array([255, 127,  39, 255], dtype=np.uint8)
    sprite = img[sy:sy+16, sx:sx+16]
    sprite = np.where((sprite == alpha_v).all(axis=2).reshape(16,16,1), np.array([[[0,0,0,0]]]), sprite).astype(np.uint8)
    mask = (sprite[:,:,3] != 0)
    return sprite[:,:,:3], mask

def game_coord_to_pixel_coord(
    x, y, map_idx, base_y):
    
    global_offset = np.array([1056-16*12, 331]) #np.array([790, -29])
    map_offsets = {
        # https://bulbapedia.bulbagarden.net/wiki/List_of_locations_by_index_number_(Generation_I)
        0: np.array([0,0]), # pallet town
        1: np.array([-10, 72]), # viridian
        2: np.array([-10, 180]), # pewter
        12: np.array([0, 36]), # route 1
        13: np.array([0, 144]), # route 2
        14: np.array([30, 172]), # Route 3
        15: np.array([80, 190]), #Route 4
        33: np.array([-50, 64]), # route 22
        37: np.array([-9, 2]), # red house first
        38: np.array([-9, 25-32]), # red house second
        39: np.array([9+12, 2]), # blues house
        40: np.array([25-4, -6]), # oaks lab
        41: np.array([30, 47]), # Pokémon Center (Viridian City)
        42: np.array([30, 55]), # Poké Mart (Viridian City)
        43: np.array([30, 72]), # School (Viridian City)
        44: np.array([30, 64]), # House 1 (Viridian City)
        47: np.array([21,136]), # Gate (Viridian City/Pewter City) (Route 2)
        49: np.array([21,108]), # Gate (Route 2)
        50: np.array([21,108]), # Gate (Route 2/Viridian Forest) (Route 2)
        51: np.array([-35, 137]), # viridian forest
        52: np.array([-10, 189]), # Pewter Museum (floor 1)
        53: np.array([-10, 198]), # Pewter Museum (floor 2)
        54: np.array([-21, 169]), #Pokémon Gym (Pewter City)
        55: np.array([-19, 177]), #House with disobedient Nidoran♂ (Pewter City)
        56: np.array([-30, 163]), #Poké Mart (Pewter City)
        57: np.array([-19, 177]), #House with two Trainers (Pewter City)
        58: np.array([-25, 154]), # Pokémon Center (Pewter City)
        59: np.array([83, 227]), # Mt. Moon (Route 3 entrance)
        60: np.array([123, 227]), # Mt. Moon
        61: np.array([152, 227]), # Mt. Moon
        68: np.array([65, 190]), # Pokémon Center (Route 4)
        #193: None # Badges check gate (Route 22)
        193: np.array([-47, 72]) # Badges check gate (Route 22)
    }
    if map_idx in map_offsets.keys():
        offset = map_offsets[map_idx]
    else:
        offset = np.array([0,0])
        x, y = 0, 0
    coord = global_offset + 16*(offset + np.array([x,y]))
    coord[1] = base_y - coord[1]
    return coord

def add_sprite(overlay_map, colormap, sprite, coord, config):
    raw_base = (overlay_map[coord[1]:coord[1]+16, coord[0]:coord[0]+16, :])
    if config['sprite_colors']:
        sprite_img = np.ceil(colormap * sprite[0]).astype(np.uint8)
    else:
        sprite_img = sprite[0]
    intermediate = np.where(sprite[1][:,:,None], sprite_img, raw_base)
    overlay_map[coord[1]:coord[1]+16, coord[0]:coord[0]+16, :] = intermediate

def render_video(all_coords, walks, bg, config, file_path, queue, inter_steps=4, add_start=True):
    #print(f'processing chunk with shape {all_coords.shape}')

    debug = False
    errors = []
    img_count = 0
    sprites_rendered = 0
    turbo_map = mpl.colormaps['turbo'].resampled(8)
    file_path.mkdir()
    
    step_count = len(all_coords)
    state = [{'dir': 0, 'map': 40} for _ in all_coords[0]]
    for idx in range(step_count):
        step = all_coords[idx]
        if idx > 0:
            prev_step = all_coords[idx-1]
        elif add_start:
            prev_step = np.tile(np.array([5, 3, 40]), (all_coords.shape[1], 1))
        else:
            prev_step = all_coords[idx]
        if debug:
            pass
            #print('-- step --')
        for fract in np.arange(0,1,1/inter_steps):
            over = np.zeros_like(bg, dtype=np.uint8)
            for run in range(len(step)):
                cur = step[run]
                prev = prev_step[run]
                # cast to regular int from uint8
                cx, cy, px, py = map(int, [cur[0], cur[1], prev[0], prev[1]])
                dx = cx - px
                dy = cy - py
                total_delta = abs(dx) + abs(dy)
                if total_delta > 1:
                    state[run]['map'] = cur[2]
                dx = min(max(dx, -1), 1)
                dy = -1*min(max(dy, -1), 1)
                if debug:
                    print(f'x: {cx} y: {cy} dx: {dx} dy: {dy}')
                # only change direction if not moving between maps
                if cur[2] == prev[2]:
                    if dx > 0:
                        state[run]['dir'] = 3
                    elif dx < 0:
                        state[run]['dir'] = 2
                    elif dy > 0:
                        state[run]['dir'] = 1
                    elif dy < 0:
                        state[run]['dir'] = 0

                p_coord = game_coord_to_pixel_coord(
                    cx, -cy, state[run]['map'], over.shape[0]
                )
                prev_p_coord = game_coord_to_pixel_coord(
                    px, -py, prev[2], over.shape[0]
                )
                diff = p_coord - prev_p_coord
                interp_coord = prev_p_coord + (fract*(diff.astype(np.float32))).astype(np.int32)
                if np.linalg.norm(diff) > 16:
                    continue
                agent_version_float = run / all_coords.shape[1]
                error = add_sprite(
                    over, np.array(turbo_map(agent_version_float))[:3], walks[state[run]['dir']],
                    interp_coord, config
                )
                if error is not None:
                    errors.append(error)
                else:
                    sprites_rendered += 1
            image = np.where(over !=0, over, bg)
            cv2.imwrite(str(file_path / f'image_{img_count}.png'), image)
            img_count += 1
        update_frequency = 10
        if idx % update_frequency == 0:
            queue.put(update_frequency)
            

def update_progress_bar(queue, total):
    with tqdm(total=total) as pbar:
        while True:
            message = queue.get()
            if message == 'Done':
                print('All Images Processed')
                break
            pbar.update(message)



if __name__ == '__main__':
    '''
    session: (str) Session_ID you want to use to create a video

    episode_length: (int) Length of each episode (max_steps)
    num_cpu: (int) Number of CPU threads to use
    map_visible: (bool) Set to True to show the Kanto Region Map
    background_color: (tuple) (R,G,B) Color displayed in the background of the video
    sprite_colors: (bool) If true, adds color to sprites
    file_format: (str) Extension to use when saving the video
    video_codec: (str) Video codec used when saving the video
    fps: (int) Video playback speed in Frames Per Second
    '''
    config = {
                'session': 'session_0b2ee8ab', 'num_cpu': 10, 'episode_length': 20480, 
                'map_visible':True, 'background_color': (30,30,30), 'sprite_colors': True, 
                'file_format': '.avi', 'video_codec': 'XVID', 'fps': 30, 
            }
    run_dir = Path('baselines/' + config['session'] + '/')
    vid_dir = run_dir / 'videos'
    vid_dir.mkdir(parents=True, exist_ok=True)
    
    coords_save_pth = run_dir / '_base_coords.npz'
    
    if coords_save_pth.is_file():
        print(f'{coords_save_pth} found, loading from file')
        base_coords = np.load(coords_save_pth)['arr_0']
    else:
        print(f'{coords_save_pth} not found, building...')
        dfs = []
        print(run_dir)
        print(dir(run_dir.glob('*.gz')))
        for run in tqdm(run_dir.glob('*.gz')):
            tdf = pd.read_csv(run, compression='gzip')
            dfs.append(tdf[tdf['map'] != 'map'])

        base_coords = make_all_coords_arrays(dfs)
        print(f'saving {coords_save_pth}')
        np.savez_compressed(coords_save_pth, base_coords)
    
    print(f'initial data shape: {base_coords.shape}')

    main_map = np.array(Image.open('visualization/poke_map/pokemap_full_calibrated_CROPPED_1.png'))
    chars_img = np.array(Image.open('visualization/poke_map/characters.png'))
    walks = [get_sprite_by_coords(chars_img, x, 0) for x in [1, 4, 6, 8]]

    if config['map_visible']:
        main_map_alpha = main_map.copy()
        alpha_zero_indices = (main_map_alpha[:,:,3] == 0)
        main_map_alpha[alpha_zero_indices] = config['background_color'] + (0,)
        main_map_rgb = main_map_alpha[:,:,:3]
    else:
        main_map_rgb = np.zeros_like(main_map)[:,:,:3]
        main_map_rgb[:] = config['background_color']


    run_steps = config['episode_length']# + 1
    base_data = rearrange(base_coords, '(v s) r c -> s (v r) c', v=base_coords.shape[0]//run_steps)
    print(f'base_data shape: {base_data.shape}')
    
    #Break down videos into smaller chunks
    chunk_size = config['episode_length']//config['num_cpu']

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)

        #Create Progress Bar Process
        manager = Manager()
        queue = manager.Queue()
        progress_bar_process = Process(target=update_progress_bar, args=(queue, base_data.shape[0]))
        progress_bar_process.start()

        #Create PNG Images to Be Used in the Video 
        with Pool(config['num_cpu']) as p:
            images = p.starmap(
                render_video, [(base_data[chunk_size*i:chunk_size*(i+1)], walks, main_map_rgb, config, temp_dir_path / f'cpu_{i}', queue) for i in range(config['num_cpu'])]
            )
        
        #End Progress Bar Process
        queue.put('Done')
        progress_bar_process.join()

        #Create Video
        fname = vid_dir / f'video_{config["file_format"]}'
        fourcc = cv2.VideoWriter_fourcc(*config['video_codec'])
        video = cv2.VideoWriter(str(fname), fourcc, config['fps'], main_map_rgb.shape[:2])

        subfolders = sorted([folder for folder in temp_dir_path.iterdir() if folder.is_dir()], key=lambda x: int(x.name.split('_')[1]))
        for folder in subfolders:
            image_files = sorted(folder.glob('*.png'), key=lambda x: int(x.stem.split('_')[1]))
            for filename in image_files:
                img = cv2.imread(str(filename))
                video.write(img)
        video.release()
