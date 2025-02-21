"""
Preprocess dataset for sokoban task - see rage/env/sokoban/sokoban.py for details
The script filter the generated sokoban task by limiting the number of steps.
Uses multiprocessing for faster data generation.
"""

import re
import os
import json
from datasets import Dataset, load_dataset
from random import randint, seed, choice
from typing import List, Tuple
from tqdm import tqdm
from verl.utils.hdfs_io import copy, makedirs
import argparse
import datasets
from collections import defaultdict
from ragen.env.sokoban import SokobanEnv
from ragen.env.sokoban.room_utils import get_shortest_action_path
import multiprocessing as mp
from functools import partial

INSTRUCTION_TEMPLATE = """You are a Sokoban solver.

Sokoban Quick Guide
Goal: Push all boxes (X) onto targets (O).

Symbols:
# Wall | _ Floor | O Target | X Box | P You | √ = Box on Target | S = You on Target

Rules:
1. Push boxes (can't pull).
2. Avoid walls (#).

Actions you can take: Up, Down, Left, Right. You can only take one action at a time.

Rewards:
Move: -0.1
Box on target: +1.0
All boxes placed: +10.0


[Cumulative Observations]:
{observation}
Decide the next action:\
"""

qwen_instruct = """\
<|im_start|>system
You are a helpful assistant. You first thinks about the reasoning process in the mind and then provides the user with the answer.
<|im_end|>
<|im_start|>user
{prompt}
Show your work in <think> </think> tag. And return the final unique answer in only one <answer> </answer> tag, for example <answer> Up/Down/Left/Right </answer>. <|im_end|>
<|im_start|>assistant
<think>\
"""

base = """\
A conversation between User and Assistant. The user asks a question, and the Assistant solves it. \
The assistant first thinks briefly about the reasoning process in the mind and then provides the user with the answer.
User: {prompt}
Show your work in <think> </think> tags. And return the final answer in <answer> </answer> tags, for example <think> [Thoughts] </think> <answer> 1 </answer>
Assistant: 
<think>
"""

templates = {
    'qwen-instruct': qwen_instruct,
    'base': base
}

# templates = {
#     'qwen-instruct': '<|im_start|>user\n{prompt}\nAlways output: <think> [Your thoughts] </think> <answer> [your answer] </answer> with no extra text. Strictly follow this format. <|im_end|>\n<|im_start|>assistant\n<think>',
#     'base': 'A conversation between User and Assistant. The user asks a question, and the Assistant solves it. The assistant first thinks briefly about the reasoning process in the mind and then provides the user with the answer.\nUser: {prompt}\nShow your work in <think> </think> tags. And return the final answer in <answer> </answer> tags, for example <think> [Thoughts] </think> <answer> 1 </answer>\nAssistant: \n<think>'
# }


def process_seed(seed, env_params):
    """Process a single seed to generate training data"""
    env = SokobanEnv(
        dim_room=env_params['dim_room'],
        num_boxes=env_params['num_boxes'],
        max_steps=env_params['max_steps'],
        search_depth=env_params['search_depth']
    )
    observation = env.reset(seed=seed, mode='tiny_rgb_array')
    gt_action_sequence = get_shortest_action_path(env.room_fixed, env.room_state, MAX_DEPTH=100)
    
    if gt_action_sequence is None or len(gt_action_sequence) > 1:
        return None, None
        
    instruction = INSTRUCTION_TEMPLATE.format(observation=observation)
    return seed, (instruction, gt_action_sequence)

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Generate trajectories using specified environment and policy.")
    parser.add_argument("--env", type=str, default="sokoban", help="Environment name (default: 'sokoban').")
    parser.add_argument("--algo", type=str, default="bfs", choices=["bfs"], help="Algorithm to use (default: 'bfs').")
    parser.add_argument("--seed", type=int, default=10000, help="Seed for random number generation (default: 10000).")
    parser.add_argument("--output", type=str, default="data/sokoban_easy", help="Output file to save the trajectories (default: 'data/sokoban').")
    parser.add_argument("--train_size", type=int, default=50000, help="Number of trajectories to generate (default: 10000).")
    parser.add_argument("--test_size", type=int, default=2500, help="Number of trajectories to generate (default: 500).")
    parser.add_argument("--bfs_max_nodes", type=int, default=1000, help="Maximum number of nodes to use for BFS (default: 100000).")
    parser.add_argument("--prefix", type=str, default='qwen-instruct', choices=['qwen-instruct', 'base'])
    parser.add_argument("--num_workers", type=int, default=mp.cpu_count(), help="Number of worker processes")

    args = parser.parse_args()
    
    assert args.env == "sokoban", f"Unsupported environment: {args.env}"  # Fixed f-string
    assert args.algo == "bfs", f"Unsupported algorithm: {args.algo}"  # Fixed f-string
    data_source = args.env
    
    dim_x, dim_y, num_boxes, max_steps, search_depth = 6, 6, 1, 10, 30
    env_params = {
        'dim_room': (dim_x, dim_y),
        'num_boxes': num_boxes,
        'max_steps': max_steps,
        'search_depth': search_depth
    }
    
    seeds = range(args.seed, args.seed + args.train_size + args.test_size)
    train_set, test_set = [], []
    action_counter = defaultdict(int)

    # Set up multiprocessing pool
    pool = mp.Pool(processes=args.num_workers)
    process_fn = partial(process_seed, env_params=env_params)
    
    # Process seeds in parallel with progress bar
    results = list(tqdm(pool.imap(process_fn, seeds), total=len(seeds)))
    pool.close()
    pool.join()

    # Process results
    for seed, result in results:
        if result is None:
            continue
        instruction, gt_action_sequence = result
        
        for action in gt_action_sequence:
            action_counter[action] += 1
            
        if seed < args.seed + args.train_size:
            train_set.append((seed, instruction))
        else:
            test_set.append((seed, instruction))
    
    print(action_counter)
    def _create_instance(idx, instruction):
        prompt_formatted = templates[args.prefix].format(prompt=instruction)
        return {
            "data_source": data_source,
            "prompt": [{"role": "user", "content": prompt_formatted}],
            "ability": "bfs",
            "reward_model": {"style": "rule", "ground_truth": {"target": 0, "numbers": [0, 0]}},
            "extra_info": {"split": "train", "index": idx}
        }

    train_dataset = Dataset.from_list([_create_instance(seed, instruction) for seed, instruction in train_set])
    test_dataset = Dataset.from_list([_create_instance(seed, instruction) for seed, instruction in test_set])

    def make_map_fn(split):
        def process_fn(example, idx):
            return example
        return process_fn
    
    train_dataset = train_dataset.map(function=make_map_fn('train'), with_indices=True)
    test_dataset = test_dataset.map(function=make_map_fn('test'), with_indices=True)

    # Create output directory if it doesn't exist
    os.makedirs(args.output, exist_ok=True)

    train_dataset.to_parquet(os.path.join(args.output, 'train.parquet'))
    test_dataset.to_parquet(os.path.join(args.output, 'test.parquet'))

if __name__ == "__main__":
    main()