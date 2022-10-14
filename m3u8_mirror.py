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
from datetime import datetime, timedelta

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
    if url.startswith('http://') or url.startswith('https://'):
        urlgroup = urlparse(url)
        return urlgroup.scheme + '://' + urlgroup.hostname

def get_m3u8_body(url):
    print('read m3u8 file:', url)
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    try:
        r = session.get(url, timeout=10)
    except requests.exceptions.RequestException as e:
        raise SystemExit(e)

    if r.ok:
        return r.text
    else:
        exit_error(r.status_code, r.text)

def manifest_parse_key(data):
    separator = 'URI='
    key = data.split(separator).pop(0) + separator
    url = data.split(separator).pop().strip('"')
    return {'key': key, 'url': url, 'raw': data}

def get_segment_count(key, segment_counter, timestamp):
    if '#EXTINF' in key:
        #EXTINF:6.00000,
        segment_counter += 1
        timestamp += timedelta(seconds = float(key[key.rfind(':')+1:-1]))
    return segment_counter, timestamp

def get_manifest_timestamp(key):
    #EXT-X-PROGRAM-DATE-TIME:2022-10-11T23:45:50.840Z # UTC
    timestamp = key[key.find(':')+1:]
    timestamp = datetime.fromisoformat(timestamp[:-1])
    return timestamp, {'key': key, 'timestamp': timestamp}

def manifest_parse_fragment(host, manifest, index, segment_counter, timestamp):
    line = manifest[index]
    keys = []
    url = None
    data = {'segment_counter': segment_counter, 'timestamp': timestamp}

    if line.startswith('#'):
        keys.append(line)
        logging.debug(f'parsing: line0: {line}')
        segment_counter, timestamp = get_segment_count(line, segment_counter, timestamp)

        index += 1
        while index < len(manifest):
            line = manifest[index]
            logging.debug(f'parsing: index(N+1): {index}')
            logging.debug(f'parsing: line(N+1): {line}')
            if line.startswith('#'):
                keys.append(line)
                index += 1
                segment_counter, timestamp = get_segment_count(line, segment_counter, timestamp)
            else:
                if not line.startswith('http'):
                    line = f'{host}/{url}'

                data['key'] = keys
                data['url'] = line
                data['segment_counter'] = segment_counter
                data['timestamp'] = timestamp
                logging.debug(f'parsed: {data}')
                return index, data, segment_counter, timestamp

def get_manifest_details(host, manifest):
    header = []
    body = []
    segment_counter = 0
    timestamp = datetime.fromtimestamp(0)
    index = 0
    manifest = manifest.split('\n')
    while index < len(manifest):
        line = manifest[index]
        if line in ['#EXTM3U']:
            header.append(line)
        elif '#EXT-X-MEDIA-SEQUENCE' in line:
            header.append(line)
            segment_counter = int(line[line.rfind(':')+1:])
        elif line[:line.rfind(':')] in ['#EXT-X-VERSION', '#EXT-X-TARGETDURATION', '#EXT-X-DISCONTINUITY-SEQUENCE']:
            header.append(line)
        elif '#EXT-X-KEY:METHOD=AES-128' in line:
            body.append(manifest_parse_key(line))
        elif '#EXT-X-PROGRAM-DATE-TIME' in line:
            timestamp, parsed_key = get_manifest_timestamp(line)
            body.append(parsed_key)

        elif line.startswith('#'):
            index, parsed_manifest, segment_counter, timestamp = manifest_parse_fragment(host, manifest, index, segment_counter, timestamp)
            body.append(parsed_manifest)
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

def findAnyStringInList(text, text_list):
    return any(text in item for item in text_list)

def mirror_manifest(header, body, download_dir):
    ts_path_list = []
    manifest = f'{download_dir}/manifest.m3u8'

    if not os.path.isfile(manifest):
        write_manifest(manifest, header)

    for line in body:
        key = line.get('key')
        url = line.get('url')
        logging.debug(f'parsed manifest line: {line}')
        if findAnyStringInList('#EXTINF', key):
            path = download_data(url, download_dir)
            if path is not None:
                if isinstance(key, str):
                    key = [key]
                write_manifest(manifest, key + [path])
        elif '#EXT-X-KEY:METHOD=AES-128' in key:
            path = download_data(url, download_dir)
            write_manifest(manifest, [key + path])
        elif 'KEL_NEWLINE' in key:
            write_manifest(manifest, [''])
        else:
            logging.debug(f'write as other: {line}')
            write_manifest(manifest, key)

def download_data(url, download_dir):
    logging.debug(f'starting downloading: {url}')

    if url.startswith('https://dai.google.com'):
        if '/slate/' in url:
            path = download_googledai_data(url, download_dir, '%s/slate_%s.ts')
        else:
            path = download_googledai_data(url, download_dir, '%s/adpod_%s.ts')
    elif 'serve.key' in url:
        path = download_googledai_data(url, download_dir, '%s/serve_%s.key')
    else:
        path = download_main_data(url, download_dir)
    return path

def write_binary(path, data):
    with open(path, 'wb') as f:
        f.write(data)

def exit_error(code, description):
    logging.error(code)
    logging.error(description)
    raise SystemExit(code)

def download_googledai_data(url, download_dir, filename_format):
    r = requests.get(url)
    if r.ok:
        curr_path = filename_format % (download_dir, sha1sum(r.content))

        if os.path.isfile(curr_path):
            logging.warning('adpod/key file already exist')
        else:
            write_binary(curr_path, r.content)

        return curr_path
    else:
        exit_error(r.status_code, r.text)

def download_main_data(url, download_dir):
    file_name = url.split('/').pop()
    if '?' in file_name:
        file_name = f'{file_name.split("?")[0]}'
    curr_path = '%s/%s' % (download_dir, file_name)

    if os.path.isfile(curr_path):
        logging.warning('file already exist')
    else:
        r = requests.get(url)
        if r.ok:
            write_binary(curr_path, r.content)
        else:
            exit_error(r.status_code, r.text)
        return curr_path

def check_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

        with open(f'{path}/.gitignore', 'w') as f:
            f.write('*')

def get_download_url_list(host, m3u8_url, url_list = []):
    if m3u8_url.startswith('http://') or m3u8_url.startswith('https://'):
        body = get_m3u8_body(m3u8_url)
    else:
        with open(m3u8_url, 'r') as f:
            body = f.read()
    # if True:
    #     write_manifest('temp.m3u8', body)

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
            logging.FileHandler(filename, 'w', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

if __name__ == '__main__':
    configure_logging()
    config = get_cfg()
    if config:
        download_ts(config[0], config[1])
