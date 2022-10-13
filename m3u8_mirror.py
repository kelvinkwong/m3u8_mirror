#!/usr/bin/env python3

import os
import sys
import requests
from urllib.parse import urlparse
import shutil
import re
import time
import glob

import hashlib
import mmap
import logging

def sha1sum(data):
    h = hashlib.sha1()
    h.update(data)
    return h.hexdigest()

def get_cfg():
    argv = sys.argv
    if len(argv) <= 1:
        print('Usage: python3', argv[0], '[your_m3u8_url] [save_dir]')
        print('Sample: python3', argv[0], 'https://xxx.com/video.m3u8', '/Users/huzhenjie/Downloads/save_dir')
        print('or Usage: python3', argv[0], 'path_to_file_with_save_dir_and_manifest')
        return None

    if os.path.isfile(argv[1]):
        with open(argv[1]) as f:
            save_dir = f.readline().rstrip('\n') + '/'
            manifest = f.readline().rstrip('\n')
        return (manifest, save_dir)
    else:
        return (argv[1], argv[2])

def get_host(url):
    urlgroup = urlparse(url)
    return urlgroup.scheme + '://' + urlgroup.hostname

def get_m3u8_body(url):
    print('read m3u8 file:', url)
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=10)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    r = session.get(url, timeout=10)
    return r.text

def get_url_list(host, body):
    lines = body.split('\n')
    ts_url_list = []
    for line in lines:
        if not line.startswith('#') and line != '':
            if line.lower().startswith('http'):
                ts_url_list.append(line)
            else:
                ts_url_list.append('%s/%s' % (host, line))
    return ts_url_list

# def get_manifest_details(host, manifest):
#     header = []
#     body = []
#     for line in manifest.split('\n'):
#         if line in ['#EXTM3U']:
#             header.append(line)
#         elif line[:line.rfind(':')] in ['#EXT-X-VERSION', '#EXT-X-TARGETDURATION', '#EXT-X-MEDIA-SEQUENCE', '#EXT-X-DISCONTINUITY-SEQUENCE']:
#             header.append(line)
#
#         elif line.startswith('#') or line == '':
#             body.append(line)
#         elif line.lower().startswith('http'):
#             body.append(line)
#         else:
#             body.append('%s/%s' % (host, line))
#     return header, body

def manifest_parse_key(data):
    separator = 'URI='
    key = data.split(separator).pop(0) + separator
    url = data.split(separator).pop().strip('"')
    return {'key': key, 'url': url, 'raw': data}

def manifest_parse_fragment(host, key, url):
    logging.debug(f'host: {host}, key: {key}, url: {url}')
    if not url.startswith('http'):
        url = f'{host}/{url}'
    data = {'key': key, 'url': url}
    logging.debug(f'parsed: {data}')
    return data

def get_manifest_details(host, manifest):
    header = []
    body = []

    index = 0
    manifest = manifest.split('\n')
    while index < len(manifest):
        line = manifest[index]
        if line in ['#EXTM3U']:
            header.append(line)
        elif line[:line.rfind(':')] in ['#EXT-X-VERSION', '#EXT-X-TARGETDURATION', '#EXT-X-MEDIA-SEQUENCE', '#EXT-X-DISCONTINUITY-SEQUENCE']:
            header.append(line)

        elif '#EXTINF' in line:
            index += 1
            next_line = manifest[index]
            body.append(manifest_parse_fragment(host, line, next_line))
        elif '#EXT-X-KEY:METHOD=AES-128' in line:
            body.append(manifest_parse_key(line))

        elif line.startswith('#'):
            body.append({'key': line})
        elif line == '':
            body.append({'key': 'KEL_NEWLINE'})
        else:
            body.append({'key': 'other', 'data': line})

        index += 1

    return header, body

def write_manifest(filepath, data):
    if isinstance(data, str):
        data = [data]

    with open(filepath, 'a') as f:
        for d in data:
            logging.debug(f'writing: {d}')
            f.write(d)
            f.write('\n')

def mirror_manifest(header, body, download_dir):
    ts_path_list = []
    manifest = f'{download_dir}/manifest.m3u8'

    if not os.path.isfile(manifest):
        write_manifest(manifest, header)

    for line in body:
        key = line.get('key')
        url = line.get('url')
        logging.debug(f'parsed manifest line: {line}')
        if '#EXTINF' in key:
            path = download_data(url, download_dir)
            if path is not None:
                write_manifest(manifest, [key, path])
        elif '#EXT-X-KEY:METHOD=AES-128' in key:
            path = download_data(url, download_dir)
            write_manifest(manifest, [key + path])
        elif 'KEL_NEWLINE' in key:
            write_manifest(manifest, [''])
        else:
            write_manifest(manifest, key)

def download_data(url, download_dir):
    if url.startswith('https://dai.google.com'):
        path = download_googledai_data(url, download_dir, '%s/adpod_%s.ts')
    elif 'serve.key' in url:
        path = download_googledai_data(url, download_dir, '%s/serve_%s.key')
    else:
        path = download_main_data(url, download_dir)
    return path

def download_googledai_data(url, download_dir, filename_format):
    r = requests.get(url)
    curr_path = filename_format % (download_dir, sha1sum(r.content))

    print('\n[process]')
    print('[download]:', url)
    print('[target]:', curr_path)

    if os.path.isfile(curr_path):
        print('[warn]: adpod/key file already exist')
    else:
        with open(curr_path, 'wb') as f:
            f.write(r.content)
    return curr_path

def download_main_data(url, download_dir):
    file_name = url.split('/').pop()
    if '?' in file_name:
        file_name = f'{file_name.split("?")[0]}'
    curr_path = '%s/%s' % (download_dir, file_name)

    print('\n[process]')
    print('[download]:', url)
    print('[target]:', curr_path)

    if os.path.isfile(curr_path):
        print('[warn]: file already exist')
    else:
        r = requests.get(url)
        with open(curr_path, 'wb') as f:
            f.write(r.content)
        return curr_path

def check_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

        with open(f'{path}/.gitignore', 'w') as f:
            f.write('*')

# def get_download_url_list(host, m3u8_url, url_list = []):
#   body = get_m3u8_body(m3u8_url)
#   ts_url_list = get_url_list(host, body)
#   for url in ts_url_list:
#       if url.lower().endswith('.m3u8'):
#           url_list = get_download_url_list(host, url, url_list)
#       else:
#           url_list.append(url)
#   return url_list

def get_download_url_list(host, m3u8_url, url_list = []):
    body = get_m3u8_body(m3u8_url)
    logging.debug(body)
    return get_manifest_details(host, body)

def download_ts(m3u8_url, save_dir):
    check_dir(save_dir)
    host = get_host(m3u8_url)
    while True:
        header, body = get_download_url_list(host, m3u8_url)
        mirror_manifest(header, body, save_dir)

        logging.info(f'total line count: {len(body)}')
        sleep = 30
        print(f'[info]: sleeping {sleep}s')
        time.sleep(sleep)

def configure_logging(level = logging.DEBUG, filename = f'{sys.argv[0]}.log'):
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(filename)s +%(lineno)d - %(message)s",
        handlers=[
            logging.FileHandler(filename, 'w'),
            logging.StreamHandler()
        ]
    )

if __name__ == '__main__':
    configure_logging()
    config = get_cfg()
    if config:
        download_ts(config[0], config[1])
