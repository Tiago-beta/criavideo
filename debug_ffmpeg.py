import sys
sys.path.insert(0, '/opt/levita-video')

from app.config import get_settings
settings = get_settings()

width, height = 1920, 1080
scenes = []
for i in range(6):
    scenes.append({
        'scene_index': i,
        'image_path': f'/opt/levita-video/media/images/6/scene_{i:03d}.png',
        'start_time': i * 35,
        'end_time': (i + 1) * 35,
        'scene_type': 'image',
    })

input_args = []
filters = []
concat_inputs = []
input_idx = 0

for i, sc in enumerate(scenes):
    dur = sc['end_time'] - sc['start_time']
    frames = int(dur * 30)
    
    input_args.extend(['-loop', '1', '-t', str(dur), '-i', sc['image_path']])
    effect = i % 3
    if effect == 0:
        filters.append(
            f"[{input_idx}:v]scale={width*2}:{height*2},"
            f"zoompan=z='min(zoom+0.001,1.4)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={width}x{height}:fps=30,"
            f"setpts=PTS-STARTPTS[v{i}]"
        )
    elif effect == 1:
        zoom_rate = 0.4 / max(frames, 1)
        filters.append(
            f"[{input_idx}:v]scale={width*2}:{height*2},"
            f"zoompan=z='max(1.4-on*{zoom_rate:.6f},1.0)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={width}x{height}:fps=30,"
            f"setpts=PTS-STARTPTS[v{i}]"
        )
    else:
        filters.append(
            f"[{input_idx}:v]scale={width*2}:{height*2},"
            f"zoompan=z='1.2':"
            f"x='on*2':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={width}x{height}:fps=30,"
            f"setpts=PTS-STARTPTS[v{i}]"
        )
    
    concat_inputs.append(f'[v{i}]')
    input_idx += 1

filter_str = ';\n'.join(filters)
concat = ''.join(concat_inputs) + f'concat=n={len(scenes)}:v=1:a=0[slideshow]'
filter_complex = f'{filter_str};\n{concat}'
print('=== FILTER COMPLEX ===')
print(filter_complex)
print()
print(f'Number of inputs: {input_idx}')
print(f'Number of scenes: {len(scenes)}')
for i, a in enumerate(input_args):
    print(f'  arg[{i}]: {a}')
