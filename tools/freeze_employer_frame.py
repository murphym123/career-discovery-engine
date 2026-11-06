"""Acquire and freeze one public DC CBE employer frame, then select a dev batch."""
import argparse
import hashlib
from pathlib import Path
from career_engine.employer_frame import FrameTransport,acquire_cbe,select_batch
from career_engine.registry_state import atomic_json


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--size',type=int,default=30);p.add_argument('--seed',default='issue25-development-v1');a=p.parse_args(argv)
    if not 1<=a.size<=40:p.error('Select 1–40 employers')
    t=FrameTransport(max_requests=4,max_bytes=4_000_000,max_seconds=120)
    frame=acquire_cbe(t);atomic_json(a.output_dir/'frame.json',frame)
    frame['input_sha256']=hashlib.sha256((a.output_dir/'frame.json').read_bytes()).hexdigest()
    batch=select_batch(frame,a.size,a.seed);atomic_json(a.output_dir/'batch.json',batch)
    print({'frame_entities':len(frame['rows']),'selection':batch['selection']})


if __name__=='__main__':main()
