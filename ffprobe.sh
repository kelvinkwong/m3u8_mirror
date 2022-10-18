#!/bin/bash

ffprobe $1 -show_packets -print_format csv=nokey=0 -hide_banner
