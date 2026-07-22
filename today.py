import datetime
import hashlib
import os
import time

import requests
from dateutil import relativedelta
from lxml import etree

HEADERS = {'authorization': 'token ' + os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME']
BIRTHDAY = datetime.datetime(2002, 11, 17)  # <-- set your real birth date

QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0,
               'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


def query_count(k):
    QUERY_COUNT[k] += 1


def format_plural(unit):
    return 's' if unit != 1 else ''


def daily_readme(birthday):
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days),
        ' 🎂' if (diff.months == 0 and diff.days == 0) else '')


def simple_request(func_name, query, variables):
    r = requests.post('https://api.github.com/graphql',
                      json={'query': query, 'variables': variables}, headers=HEADERS)
    if r.status_code == 200:
        return r
    raise Exception(func_name, 'failed', r.status_code, r.text, QUERY_COUNT)


def user_getter(username):
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) { id createdAt }
    }'''
    r = simple_request(user_getter.__name__, query, {'login': username})
    return {'id': r.json()['data']['user']['id']}, r.json()['data']['user']['createdAt']


def follower_getter(username):
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) { followers { totalCount } }
    }'''
    r = simple_request(follower_getter.__name__, query, {'login': username})
    return int(r.json()['data']['user']['followers']['totalCount'])


def stars_counter(data):
    return sum(n['node']['stargazers']['totalCount'] for n in data)


def graph_repos_stars(count_type, owner_affiliation, cursor=None):
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges { node { ... on Repository { nameWithOwner stargazers { totalCount } } } }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    r = simple_request(graph_repos_stars.__name__, query, variables)
    if count_type == 'repos':
        return r.json()['data']['user']['repositories']['totalCount']
    if count_type == 'stars':
        return stars_counter(r.json()['data']['user']['repositories']['edges'])


def recursive_loc(owner, repo_name, data, cache_comment,
                  addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef { target { ... on Commit {
                history(first: 100, after: $cursor) {
                    totalCount
                    edges { node { ... on Commit { committedDate }
                        author { user { id } } deletions additions } }
                    pageInfo { endCursor hasNextPage }
                } } } }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    r = requests.post('https://api.github.com/graphql',
                      json={'query': query, 'variables': variables}, headers=HEADERS)
    if r.status_code == 200:
        if r.json()['data']['repository']['defaultBranchRef'] is not None:
            return loc_counter_one_repo(
                owner, repo_name, data, cache_comment,
                r.json()['data']['repository']['defaultBranchRef']['target']['history'],
                addition_total, deletion_total, my_commits)
        return 0
    force_close_file(data, cache_comment)
    if r.status_code == 403:
        raise Exception('Too many requests in a short amount of time!')
    raise Exception('recursive_loc() failed', r.status_code, r.text, QUERY_COUNT)


def loc_counter_one_repo(owner, repo_name, data, cache_comment, history,
                         addition_total, deletion_total, my_commits):
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']
    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    return recursive_loc(owner, repo_name, data, cache_comment,
                         addition_total, deletion_total, my_commits,
                         history['pageInfo']['endCursor'])


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=None):
    if edges is None:
        edges = []
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges { node { ... on Repository { nameWithOwner
                    defaultBranchRef { target { ... on Commit { history { totalCount } } } } } } }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    r = simple_request(loc_query.__name__, query, variables)
    repos = r.json()['data']['user']['repositories']
    if repos['pageInfo']['hasNextPage']:
        edges += repos['edges']
        return loc_query(owner_affiliation, comment_size, force_cache,
                         repos['pageInfo']['endCursor'], edges)
    return cache_builder(edges + repos['edges'], comment_size, force_cache)


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    cached = True
    os.makedirs('cache', exist_ok=True)
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError:
        data = []
        if comment_size > 0:
            for _ in range(comment_size):
                data.append('This line is a comment block. Write whatever you want here.\n')
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        cached = False
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size]
    data = data[comment_size:]
    for index in range(len(edges)):
        repo_hash, commit_count, *__ = data[index].split()
        if repo_hash == hashlib.sha256(
                edges[index]['node']['nameWithOwner'].encode('utf-8')).hexdigest():
            try:
                if int(commit_count) != edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']:
                    owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
                    loc = recursive_loc(owner, repo_name, data, cache_comment)
                    data[index] = (repo_hash + ' '
                                   + str(edges[index]['node']['defaultBranchRef']['target']['history']['totalCount'])
                                   + ' ' + str(loc[2]) + ' ' + str(loc[0]) + ' ' + str(loc[1]) + '\n')
            except TypeError:
                data[index] = repo_hash + ' 0 0 0 0\n'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    for line in data:
        loc = line.split()
        loc_add += int(loc[3])
        loc_del += int(loc[4])
    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    with open(filename, 'r') as f:
        data = []
        if comment_size > 0:
            data = f.readlines()[:comment_size]
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            f.write(hashlib.sha256(
                node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')


def force_close_file(data, cache_comment):
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    print('Partial cache saved to', filename)


def commit_counter(comment_size):
    total_commits = 0
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    with open(filename, 'r') as f:
        data = f.readlines()
    data = data[comment_size:]
    for line in data:
        total_commits += int(line.split()[2])
    return total_commits


def find_and_replace(root, element_id, new_text):
    el = root.find(f".//*[@id='{element_id}']")
    if el is not None:
        el.text = new_text


COLUMN_WIDTH = 63  # total rendered width of every stats row


def fmt(value):
    """Comma-format ints, pass everything else through as str."""
    if isinstance(value, int):
        return '{:,}'.format(value)
    return str(value)


def set_dots(root, element_id, gap):
    """Write a dot-leader of the given gap width into <id>_dots."""
    gap = max(0, gap)
    if gap <= 2:
        dot_string = {0: '', 1: ' ', 2: '. '}[gap]
    else:
        dot_string = ' ' + ('.' * (gap - 2)) + ' '
    find_and_replace(root, f"{element_id}_dots", dot_string)


def justify_row(root, spec):
    """
    Lay out one stats row so it ends exactly at COLUMN_WIDTH.

    spec is a list of segments, each either:
      ('lit', text)              literal text, fixed width
      ('val', element_id, text)  a value, no dots before it
      ('pad', element_id, text)  a value preceded by an elastic dot-leader

    All non-'pad' widths are summed; the leftover space is divided
    among the 'pad' segments, so the row always totals COLUMN_WIDTH.
    """
    fixed = 0
    pads = []
    for seg in spec:
        if seg[0] == 'lit':
            fixed += len(seg[1])
        elif seg[0] == 'val':
            text = fmt(seg[2])
            find_and_replace(root, seg[1], text)
            fixed += len(text)
        elif seg[0] == 'pad':
            text = fmt(seg[2])
            find_and_replace(root, seg[1], text)
            fixed += len(text)
            pads.append(seg[1])

    slack = COLUMN_WIDTH - fixed
    if not pads:
        return
    share, extra = divmod(max(0, slack), len(pads))
    for i, eid in enumerate(pads):
        set_dots(root, eid, share + (1 if i < extra else 0))


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data,
                  contrib_data, follower_data, loc_data):
    tree = etree.parse(filename)
    root = tree.getroot()
    # Uptime
    justify_row(root, [
        ('lit', '. Uptime:'),
        ('pad', 'age_data', age_data),
    ])

    # Repos: NN {Contributed: NN} | Stars: .... NN
    justify_row(root, [
        ('lit', '. Repos:'),
        ('pad', 'repo_data', repo_data),
        ('lit', ' {Contributed: '),
        ('val', 'contrib_data', contrib_data),
        ('lit', '} | Stars:'),
        ('pad', 'star_data', star_data),
    ])

    # Commits: N,NNN | Followers: ... NN
    justify_row(root, [
        ('lit', '. Commits:'),
        ('pad', 'commit_data', commit_data),
        ('lit', ' | Followers:'),
        ('pad', 'follower_data', follower_data),
    ])

    # Lines of Code on GitHub: NNN,NNN ( NNN,NNN++, NN,NNN-- )
    justify_row(root, [
        ('lit', '. Lines of Code on GitHub:'),
        ('pad', 'loc_data', loc_data[2]),
        ('lit', ' ( '),
        ('val', 'loc_add', loc_data[0]),
        ('lit', '++, '),
        ('val', 'loc_del', loc_data[1]),
        ('lit', '-- )'),
    ])
    tree.write(filename, encoding='utf-8', xml_declaration=True)


def perf_counter(funct, *args):
    start = time.perf_counter()
    out = funct(*args)
    return out, time.perf_counter() - start


if __name__ == '__main__':
    print('Calculation times:')
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    age_data, age_time = perf_counter(daily_readme, BIRTHDAY)
    total_loc, loc_time = perf_counter(
        loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7)
    commit_data, commit_time = perf_counter(commit_counter, 7)
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(
        graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)

    for i in range(len(total_loc) - 1):
        total_loc[i] = '{:,}'.format(total_loc[i])

    svg_overwrite('dark_mode.svg', age_data, commit_data, star_data,
                  repo_data, contrib_data, follower_data, total_loc[:-1])
    svg_overwrite('light_mode.svg', age_data, commit_data, star_data,
                  repo_data, contrib_data, follower_data, total_loc[:-1])

    print('Total GitHub GraphQL API calls:', sum(QUERY_COUNT.values()))
    for k, v in QUERY_COUNT.items():
        print('   {:<22}{:>6}'.format(k + ':', v))
