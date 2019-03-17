#!/bin/bash

BASE_DIR=/opt/stack
INITIAL_SED_ARG=""
GIT_CLONE_ARGS="--depth 128"
CONCURRENCY=$(( $(nproc) + 2 ))


display_help()
{
cat <<EOF

usage: $0 <options..>

This script clones the base repositories and pip install them
-h, --help: print this help
-P, --skip-pip: without pip step
-p, --skip-git: just the pip steps expecting the repos are there
-G, --git-args: Argents for git (default "--depth 128")
-n, --concurrency: Maximum number of parallel git clone/pull
-S, --intial-sed-args: allows to clone from closer mirror first
EOF
}

if ! options=$(getopt -o hG:S:n:Pp -l help,git-args:,intial-sed-args:,concurrency:,skip-pip,skip-git -- "$@")
then
    #parse error
    display_help
    exit 1
fi

eval set -- $options

DOGIT=TRUE
DOPIP=TRUE

while [ $# -gt 0 ]
do
    case "$1" in
        -h|--help) display_help; exit 0 ;;
        -P|--skip-pip) DOPIP=FALSE ;;
        -p|--skip-git) DOGIT=FALSE  ;;
        -n|--concurrency) CONCURRENCY=$2; shift;;
        -G|--git-args) GIT_CLONE_ARGS=$2; shift;;
        -I|--intial-sed-args) INITIAL_SED_ARG=$2; shift ;;
        (--) shift; break ;;
        (-*) echo "$0: error - unrecognized option $1" >&2; display_help; exit 1 ;;
        (*)  echo "$0: error - unexpected argument $1" >&2; display_help; exit 1 ;;
    esac
    shift
done

set -x

mkdir -p "$BASE_DIR"

function git_clone_or_pull() (
  local origin_url=$1
  local initial_url
  local repo_name
  if [ -n "$INITIAL_SED_ARG" ]; then
     initial_url=$(echo $origin_url | sed $INITIAL_SED_ARG)
  else
     initial_url=$origin_url
  fi
  cd "$BASE_DIR"
  repo_name=$(basename $origin_url | sed 's/[.]git$//')
  if [ -e $repo_name ]; then
     # TODO: check is it valid git repo, and make it valid at all cost
     cd "$BASE_DIR/$repo_name"
     git stash
     git pull origin master
     if [ $? -ne 0 ]; then
        return 3
     fi
  else
     cd "$BASE_DIR"
     git clone $GIT_CLONE_ARGS "$initial_url" "$repo_name"
     if [ "$initial_url" != "$origin_url" ]; then
        cd "$BASE_DIR/$repo_name"
        git remote rename origin initial
	git remote add origin "$origin_url"
	git pull origin master
	if [ $? -ne 0 ]; then
	    return 3
	fi
     fi
  fi
)


declare -A TASKS

function create_task {
   local keys
   local wpid
   local err
   local pid
   if [ ${#TASKS[@]} -ge $CONCURRENCY ]; then
       keys=(${!TASKS[@]})
       wpid=${keys[0]}
       wait $wpid
       err=$?
       if [ $err -ne 0 ]; then
	   echo "${TASKS[$wpid]} Failed" 2>&1
	   exit 2
       fi
       echo "${TASKS[$wpid]} finshed" 2>&1
       unset TASKS[$wpid]
   fi
   $* &
   pid=$!
   TASKS[$pid]="$*"
}

function wait_all {
   local wpid
   local err

   # wait for all pids
   for wpid in ${!TASKS[@]}; do
       wait $wpid
       err=$?
       if [ $err -ne 0 ]; then
          echo "${TASKS[wpid]} Failed" 2>&1
          exit 2
       fi
       echo "${TASKS[wpid]} finshed" 2>&1
   done
}

if [ $DOGIT = TRUE ]; then
   git_clone_or_pull "https://github.com/openstack/nova.git"
   git_clone_or_pull "https://github.com/openstack/neutron.git"
   git_clone_or_pull "https://github.com/openstack/glance.git"
   git_clone_or_pull "https://github.com/openstack/cinder.git"
   git_clone_or_pull "https://github.com/openstack/keystone.git"
   git_clone_or_pull "https://github.com/openstack/swift.git"
   git_clone_or_pull "https://github.com/openstack/tempest.git"
   git_clone_or_pull "https://github.com/openstack/requirements.git"
   git_clone_or_pull "https://github.com/novnc/noVNC.git"

   wait_all
fi

if [ $DOPIP = TRUE ]; then
    cd "$BASE_DIR"
    pip3 install -c requirements/upper-constraints.txt -r nova/requirements.txt
    pip3 install -c requirements/upper-constraints.txt -r neutron/requirements.txt
    pip3 install -c requirements/upper-constraints.txt -r glance/requirements.txt
    pip3 install -c requirements/upper-constraints.txt -r cinder/requirements.txt
    pip3 install -c requirements/upper-constraints.txt -r keystone/requirements.txt
    pip install -c requirements/upper-constraints.txt -r swift/requirements.txt
    pip3 install -c requirements/upper-constraints.txt -r tempest/requirements.txt
    pip3 install -c requirements/upper-constraints.txt python-openstackclient
    (cd nova; pip3 install -e .)
    (cd neutron; pip3 install -e .)
    (cd glance; pip3 install -e .)
    (cd cinder; pip3 install -e .)
    (cd keystone; pip3 install -e .)
    (cd swift; pip install -e .)
    (cd tempest; pip3 install -e .)
fi
