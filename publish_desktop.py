"""Publish an existing signed desktop package and its update feed."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.request import urlopen
import zipfile

from build_release import envelope, signing_key
from desktop_release import MAX_MANIFEST, inspect_package, verified


DEFAULT_REPOSITORY = 'Parthm07/Saarthi-Desktop-Updates'
TAG_PREFIX = 'desktop-v'


def prepare_feed(package, repository):
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Use a GitHub release repository in OWNER/REPO format.')
    manifest = inspect_package(package)
    release_version = manifest['version']
    if package.name != f'Saarthi-{release_version}.zip':
        raise ValueError('Use the original versioned Saarthi ZIP filename.')
    with zipfile.ZipFile(package) as archive:
        for filename, expected in manifest['files'].items():
            with archive.open(filename) as component:
                if hashlib.file_digest(component, 'sha256').hexdigest() != expected['sha256']:
                    raise ValueError(f'Release checksum mismatch for {filename}.')
    with package.open('rb') as source:
        package_hash = hashlib.file_digest(source, 'sha256').hexdigest()
    tag = TAG_PREFIX + release_version
    root = f'https://github.com/{repository}/releases'
    payload = {
        **manifest,
        'package_url': f'{root}/download/{tag}/{package.name}',
        'package_bytes': package.stat().st_size,
        'package_sha256': package_hash,
    }
    feed = package.with_name('latest.json')
    feed.write_bytes(envelope(payload, signing_key()))
    return feed, tag, f'{root}/latest/download/latest.json'


def gh(*arguments):
    return subprocess.check_output(['gh', *arguments], text=True)


def publish(package, feed, tag, feed_url, repository):
    info = json.loads(gh('repo', 'view', repository, '--json', 'visibility,viewerPermission'))
    if info['visibility'] != 'PUBLIC' or info['viewerPermission'] not in ('ADMIN', 'MAINTAIN', 'WRITE'):
        raise ValueError('Use a public downloads repository that you can publish releases to.')
    releases = json.loads(gh('api', '--paginate', '--slurp', f'repos/{repository}/releases'))
    requested = tuple(map(int, tag.removeprefix(TAG_PREFIX).split('.')))
    for page in releases:
        for release in page:
            published_tag = release['tag_name']
            match = re.fullmatch(r'desktop-v(\d+)\.(\d+)\.(\d+)', published_tag)
            if published_tag == tag or (match and not release['draft'] and not release['prerelease']
                                        and tuple(map(int, match.groups())) >= requested):
                raise ValueError(f'{published_tag} already exists. Use a newer release version.')
    # GitHub CLI uploads both assets to a draft before making the release public.
    gh('release', 'create', tag, str(package), str(feed), '--repo', repository,
       '--title', f'Saarthi Admin {tag.removeprefix(TAG_PREFIX)}',
       '--notes', 'Signed Saarthi Admin update for Windows.', '--latest')
    with urlopen(feed_url, timeout=30) as response:
        published = response.read(MAX_MANIFEST + 1)
    if verified(published) != verified(feed.read_bytes()):
        raise ValueError('The release was uploaded, but the public latest feed points elsewhere.')
    print(f'Published and verified: {feed_url}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--repo', default=DEFAULT_REPOSITORY)
    parser.add_argument('--prepare-only', action='store_true', help='Sign the feed locally without uploading')
    args = parser.parse_args()
    package = args.package.resolve()
    feed, tag, feed_url = prepare_feed(package, args.repo)
    print(f'Signed feed: {feed}', flush=True)
    print(f'Business laptop feed URL: {feed_url}', flush=True)
    if not args.prepare_only:
        publish(package, feed, tag, feed_url, args.repo)


if __name__ == '__main__':
    main()
