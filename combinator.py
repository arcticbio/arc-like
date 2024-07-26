'''

Combinators for manipulating 1D sequences of integers. The integer value
represents color. A motivating idea behind this library is to embody physical
intuitions by creating combinators that are analogies of the geometric/physical
world. For instance translation and reflection move blocks together, in a
sense.

'''

from typing import Callable, List, Any, Tuple
import random
from torch.utils.data import TensorDataset
import torch
from dataclasses import dataclass
from visualization import visualize_datasets

@dataclass
class Sequence:
    ''' An input-output pairing, and metadata that might be used by downstream combinators. '''
    inputs: List[Any]
    outputs: List[Any]
    metadata: Any

# A `Combinator` translates a `Sequence` to a new `Sequence`.
#
# Note: each combinator has control over both inputs and outputs
# simultaneously. This has the practical effect of combinators operating from
# the "middle out". An initial input-output pairing is generated in the
# beginning, and then the eventual pairing may mutate the input into something
# different than it was initialized to at the start. This is useful for
# instance in the denoising task, where the clean block we generate at the
# start actually needs to be the expected output, and a noised version of that
# clean block becomes the input, so we `swap` them after noising.
Combinator = Callable[[Sequence], Sequence]


##########
# Starting points
#
#   These are combinators that ignore the input sequence (IE they should be
#   used at the start of the chain) and create initial pixels

def gen_some_blocks(colors: List[int], seq_length=48, background_color=0) -> Combinator:
    """ Generate a sequence of blocks with random colors. """
    def generator(seq: Sequence) -> Sequence:
        init = [background_color] * seq_length
        current_color = background_color
        is_in_block = False
        for i in range(seq_length):
            if not is_in_block and random.random() > 0.5:  # start block
                is_in_block = True
                current_color = random.choice(colors)
            if is_in_block and random.random() > 0.8:  # terminate block
                is_in_block = False
            if is_in_block:  # paint block
                init[i] = current_color
        return Sequence(init, init, None)
    return generator


def gen_one_block(colors: List[int], seq_length=48, background_color=0) -> Combinator:
    """ Generate a sequence of a single block with a random color. """
    def generator(seq: Sequence) -> Sequence:
        block_size = 5
        start_ix = random.randint(block_size, seq_length - block_size)
        end_ix = start_ix + block_size
        color = random.choice(colors)

        init = [background_color] * seq_length
        init[start_ix:end_ix] = [color] * block_size
        return Sequence(init, init, None)
    return generator


def gen_three_blocks(colors: List[int], seq_length=48, background_color=0) -> Combinator:
    """Generate a sequence with three blocks of sizes 2, 4, and 6."""
    def generator(seq: Sequence) -> Sequence:
        init = [background_color] * seq_length
        block_sizes = [2, 4, 6]
        random.shuffle(block_sizes)

        available_positions = list(range(seq_length - max(block_sizes)))
        for size in block_sizes:
            if not available_positions:
                break
            start = random.choice(available_positions)
            color = random.choice(colors)
            init[start:start+size] = [color] * size

            # Remove overlapping positions
            available_positions = [pos for pos in available_positions
                                   if pos + size <= start or pos >= start + size]

        return Sequence(init, init, None)
    return generator


def gen_some_pixels(colors: List[int], p: float = 0.2, seq_length: int = 48) -> Combinator:
    """
    Generate a sequence with pixels scattered randomly with probability p.

    Args:
    colors (List[int]): List of available colors (excluding background color 0).
    p (float): Probability of a pixel being a non-background color. Default is 0.2.
    seq_length (int): Length of the sequence to generate. Default is 48.

    Returns:
    Combinator: A function that generates a new Sequence with randomly scattered pixels.
    """
    def generator(seq: Sequence) -> Sequence:
        init = [
            random.choice(colors) if random.random() < p else 0
            for _ in range(seq_length)
        ]
        return Sequence(init, init, None)
    return generator


##################################################
# Combinators

def compose(transformers: List[Combinator]) -> Combinator:
    """ Compose multiple sequence transformers into a single transformer. """
    def composed_transformer(seq: Sequence) -> Sequence:
        for transformer in transformers:
            seq = transformer(seq)
        return seq
    return composed_transformer


def swap(seq: Sequence) -> Sequence:
    """ Swap the input and output sequences. """
    return Sequence(seq.outputs, seq.inputs, seq.metadata)


def translate(n: int) -> Combinator:
    """ Translate the sequence by n positions. """
    def f(seq):
        outputs = seq.outputs
        new_outputs = outputs[-n:] + outputs[:-n]
        return Sequence(seq.inputs, new_outputs, seq.metadata)
    return f


def reflect(i_pivot: int) -> Combinator:
    """ Reflect the sequence about the index i_pivot. """
    def f(seq):
        outputs = seq.outputs
        new_outputs = [outputs[(-(i - i_pivot) + i_pivot) %
                               len(outputs)] for i in range(len(outputs))]
        return Sequence(seq.inputs, new_outputs, seq.metadata)
    return f


def colorshift(n: int) -> Combinator:
    """ Shift the color of each element in the sequence by n. """
    def f(seq):
        outputs = seq.outputs
        new_outputs = [outputs[i] + n if outputs[i] !=
                       0 else outputs[i] for i in range(len(outputs))]
        return Sequence(seq.inputs, new_outputs, seq.metadata)
    return f


def shrink(seq: Sequence) -> Sequence:
    """For each span of consecutive instances of a specific value, map the
    midpoint of the range to that value and the rest of the range to 0."""
    outputs = seq.outputs
    new_outputs = [0] * len(outputs)
    spans = []
    current_span = {"start": 0, "val": outputs[0], "len": 1}

    for i, v in enumerate(outputs[1:], 1):
        if v == current_span["val"]:
            current_span["len"] += 1
        else:
            spans.append(current_span)
            current_span = {"start": i, "val": v, "len": 1}
    spans.append(current_span)

    for span in spans:
        mid = span["start"] + span["len"] // 2
        new_outputs[mid % len(outputs)] = span["val"]

    return Sequence(seq.inputs, new_outputs, seq.metadata)


def expand(n: int) -> Combinator:
    """Expand each value in the input sequence to fill each pixel within `n` of it
    in the output sequence. """
    def transformer(seq: Sequence) -> Sequence:
        def mode_non_bg(s: List[int]):
            counts = [(x, s.count(x)) for x in set(s) if x != 0]
            counts.sort(key=lambda x_count: x_count[1], reverse=True)
            return counts[0][0] if counts else 0
        outputs = seq.outputs
        new_outputs = [mode_non_bg([outputs[j % len(outputs)] for j in range(i - n, i + n + 1)])
                       for i in range(len(outputs))]
        return Sequence(seq.inputs, new_outputs, seq.metadata)

    return transformer


def endpoints(seq: Sequence) -> Sequence:
    ''' Identify the start/end of each block '''
    outputs = seq.outputs
    new_outputs = [0] * len(outputs)
    spans = []
    current_span = {"start": 0, "val": outputs[0], "len": 1}
    for i, v in enumerate(outputs[1:]):
        if v == current_span["val"]:
            current_span["len"] += 1
        else:
            spans.append(current_span)
            current_span = {"start": i + 1, "val": v, "len": 1}
    spans.append(current_span)
    for span in spans:
        end0 = span["start"]
        new_outputs[end0 % len(outputs)] = span["val"]
        end1 = span["start"] + span["len"] - 1
        new_outputs[end1 % len(outputs)] = span["val"]
    return Sequence(seq.inputs, new_outputs, seq.metadata)


def collect_non_background(seq: List[int]) -> List[int]:
    """Collect all non-background colors (non-zero integers) from the sequence."""
    return [color for color in seq if color != 0]


def right_align(seq: Sequence) -> Sequence:
    """Right-align all non-background colors in the sequence."""
    outputs = seq.outputs
    non_bg_colors = collect_non_background(outputs)
    aligned_outputs = [0] * (len(outputs) - len(non_bg_colors)) + non_bg_colors
    return Sequence(seq.inputs, aligned_outputs, seq.metadata)


def add_bg_noise(p, colors: List[int], background_color: int = 0) -> Combinator:
    """Add noise to the background pixels of the output sequence."""
    def transformer(seq: Sequence) -> Sequence:
        outputs = seq.outputs
        new_outputs = [
            random.choice([0] + [c for c in colors if c != 0]) if pixel == background_color and random.random() < p else pixel
            for pixel in outputs
        ]
        return Sequence(seq.inputs, new_outputs, seq.metadata)
    return transformer


def invert_colors(seq: Sequence) -> Sequence:
    """Invert the colors in the sequence, swapping background and foreground."""
    outputs = seq.outputs
    background_color = 0
    foreground_color = next(color for color in outputs if color != background_color)

    new_outputs = [foreground_color if pixel == background_color else background_color for pixel in outputs]
    return Sequence(seq.inputs, new_outputs, seq.metadata)


def get_contiguous_blocks(seq: List[int]) -> List[Tuple[int, int, int]]:
    """
    Identify contiguous blocks in the sequence.
    Returns a list of tuples (start_index, length, color).
    """
    blocks = []
    start = 0
    for i in range(1, len(seq) + 1):
        if i == len(seq) or seq[i] != seq[start]:
            blocks.append((start, i - start, seq[start]))
            start = i
    return blocks


def remove_blocks(seq: Sequence, condition: Callable[[List[Tuple[int, int, int]]], List[Tuple[int, int, int]]]) -> Sequence:
    """
    Remove blocks from the sequence based on the given condition.
    """
    outputs = seq.outputs
    blocks = get_contiguous_blocks(outputs)
    blocks_to_remove = condition(blocks)

    new_outputs = outputs.copy()
    for start, length, _ in blocks_to_remove:
        new_outputs[start:start+length] = [0] * length

    return Sequence(seq.inputs, new_outputs, seq.metadata)


def remove_longest_blocks(seq: Sequence) -> Sequence:
    """Remove the longest contiguous blocks from the sequence."""
    def condition(blocks):
        max_length = max(block[1] for block in blocks if block[2] != 0)  # Exclude background blocks
        return [block for block in blocks if block[1] == max_length and block[2] != 0]
    return remove_blocks(seq, condition)


def remove_shortest_blocks(seq: Sequence) -> Sequence:
    """Remove the shortest contiguous blocks from the sequence."""
    def condition(blocks):
        non_bg_blocks = [block for block in blocks if block[2] != 0]
        if not non_bg_blocks:
            return []
        min_length = min(block[1] for block in non_bg_blocks)
        return [block for block in non_bg_blocks if block[1] == min_length]
    return remove_blocks(seq, condition)


def add_pivot(seq: Sequence) -> Sequence:
    """Add a pivot pixel (color 5) at a random position and store its index in metadata."""
    pivot_color = 5
    inputs = seq.inputs
    outputs = seq.outputs
    pivot_index = random.randint(0, len(outputs) - 1)
    inputs[pivot_index] = pivot_color
    new_outputs = outputs.copy()
    new_outputs[pivot_index] = pivot_color
    return Sequence(inputs, new_outputs, {"pivot_index": pivot_index})


def reflect_around_pivot(seq: Sequence) -> Sequence:
    """Reflect the sequence around the pivot point stored in metadata."""
    outputs = seq.outputs
    i_pivot = seq.metadata["pivot_index"]
    new_outputs = [outputs[(-(i - i_pivot) + i_pivot) % len(outputs)] for i in range(len(outputs))]
    return Sequence(seq.inputs, new_outputs, seq.metadata)


def repaint_max_block(seq: Sequence) -> Sequence:
    """Find the largest block and repaint all non-background pixels to that color."""
    outputs = seq.outputs
    blocks = get_contiguous_blocks(outputs)
    non_bg_blocks = [block for block in blocks if block[2] != 0]

    if not non_bg_blocks:
        return seq  # No non-background blocks found

    max_block = max(non_bg_blocks, key=lambda x: x[1])
    max_color = max_block[2]

    new_outputs = [max_color if pixel != 0 else pixel for pixel in outputs]
    return Sequence(seq.inputs, new_outputs, seq.metadata)


##########
# Demo

random.seed(42)

colors = [1, 2, 3, 4, 6, 7, 8, 9]

puzzles = [
    ('translate(4)', compose([gen_some_blocks(colors), translate(4)])),
    ('reflect(seq_len//2)', compose([gen_one_block(colors), reflect(24)])),
    ('colorshift(2)', compose([gen_some_blocks(colors), colorshift(2)])),
    ('translate(4) + reflect(seq_len//2)', compose([gen_some_blocks(colors), translate(4), reflect(24)])),
    ('translate(4) + colorshift(2)', compose([gen_some_blocks(colors), translate(4), colorshift(2)])),
    ('expand(1)', compose([gen_some_blocks(colors), expand(1)])),
    ('expand(1) expand(1)', compose([gen_some_blocks(colors), expand(1), expand(1)])),
    ('expand(1) + colorshift(2)', compose([gen_some_blocks(colors), expand(1), colorshift(2)])),
    ('expand(1) + translate(1)', compose([gen_some_blocks(colors), expand(1), translate(1)])),
    ('shrink', compose([gen_some_blocks(colors), shrink])),
    ('shrink + expand(2)', compose([gen_some_blocks(colors), shrink, expand(2)])),
    ('endpoints', compose([gen_some_blocks(colors), endpoints])),
    ('infill', compose([gen_some_blocks(colors), endpoints, swap])),
    ('expand(1) + endpoints', compose([gen_one_block(colors), expand(1), endpoints])),
    ('endpoints + expand(1)', compose([gen_one_block(colors), endpoints, expand(1)])),
    ('endpoints + expand(4) + endpoints + expand(1)', compose([gen_one_block(colors), endpoints, expand(4), endpoints, expand(1)])),
    ('right_align', compose([gen_some_pixels(colors), right_align])),
    ('denoise', compose([gen_one_block(colors), swap, add_bg_noise(0.3, colors), swap])),
    ('invert_colors', compose([gen_one_block(colors), invert_colors])),
    ('remove_longest_blocks', compose([gen_some_blocks(colors), remove_longest_blocks])),
    ('remove_shortest_blocks', compose([gen_some_blocks(colors), remove_shortest_blocks])),
    ('remove_longest + endpoints', compose([gen_some_blocks(colors), remove_longest_blocks, endpoints])),
    ('reflect-pivot', compose([gen_some_blocks(list(set(colors) - {5})), add_pivot, reflect_around_pivot])),
    ('reflect-pivot + shrink', compose([gen_one_block(list(set(colors) - {5})), add_pivot, reflect_around_pivot, shrink])),
    ('repaint-from-max-block', compose([gen_three_blocks(colors), repaint_max_block])),
]

datasets = {}
num_samples = 10
grid_width = 5
grid_height = 5
for (name, transformer) in puzzles:
    all_inputs, all_outputs = [], []
    for _ in range(num_samples):
        seq = Sequence([], [], None)
        seq = transformer(seq)
        all_inputs.append(seq.inputs)
        all_outputs.append(seq.outputs)
        inputs_tensor, outputs_tensor = torch.tensor(all_inputs), torch.tensor(all_outputs)
        datasets[name] = TensorDataset(inputs_tensor, outputs_tensor)

visualize_datasets(datasets, grid_width=grid_width, grid_height=grid_height, num_samples=num_samples)
