#!/bin/bash

for i in $( seq 1 5 )
do
  python run.py scene_dataset/config.yaml scene_dataset/dataset lm bop_toolkit/ resources/cctextures scene_dataset/output/
done
