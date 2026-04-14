#!/bin/bash

#parse any arguments passed
HELP_MESSAGE="run_hal_docker.sh valid options:"
HELP_MESSAGE+="\n\t-h, --help: display this help message"
HELP_MESSAGE+="\n\t-v: mount local directory inside of the docker"
HELP_MESSAGE+="\n\t             (e.g. '-v ${HAL_TARGET}:/home/haluser/project')"
HELP_MESSAGE+="\n\t             (default is '-v ${HAL_TARGET}:/home/haluser/project')"
HELP_MESSAGE+="\n\t--allow_multiple <unique_name>: will launch an additonal instance of the container"
HELP_MESSAGE+="\n\t                                even if an instance is already running, by giving"
HELP_MESSAGE+="\n\t                                it the specified unique name"
HELP_MESSAGE+="\n\t-r,--remove_old: stop containers with existing names and do a docker rm on them"
HELP_MESSAGE+="\n\t-i,--image: specify the image name (e.g. '-i halucinator_mmddyyyy:1.0') default: latest"
HELP_MESSAGE+="\n Note: If the specified (or default) container is already running then a new shell will beopened in the container."

IMAGE_NAME=latest
CONTAINER_NAME=halucinator
MOUNT_DIR="${HAL_TARGET}:/home/haluser/project"
while [[ $# -gt 0 ]]; do
	arg="$1"
	case $arg in
		-h|--help)
			echo -e "$HELP_MESSAGE"
			exit 0
			;;
		-v)
			MOUNT_DIR="$2"
			shift #arg
			shift #value
			;;
		--allow_multiple)
			CONTAINER_NAME=$2
			shift #arg
			shift #value
			;;
		-r|--remove_old)
			echo -e "Removing existing docker container (if it exists)"
			docker stop $CONTAINER_NAME
			docker rm $CONTAINER_NAME
			shift #arg
			;;
		-i|--image)
			echo -e "Using docker image ${2}"
			IMAGE_NAME=$2
			shift #arg
			shift #value
			;;
		*)
			echo "[$0] Invalid argument: $1"
			echo -e "$HELP_MESSAGE"
			exit 1
			;;
	esac
done

#determine if an instance with the specified container name is already running
INSTANCE_COUNT=$(docker container stats --no-stream $CONTAINER_NAME 2> /dev/null | grep -c $CONTAINER_NAME)
set -e
if [[ $INSTANCE_COUNT == 0 ]]; then
		NEW_CONTAINER=1
	echo "**Mounting $MOUNT_DIR in the container**"
else
		NEW_CONTAINER=0
	echo "**OPENING A NEW SHELL INSIDE EXISTING CONTAINER $CONTAINER_NAME**"
	echo "**To open a new shell inside a different container use \"docker exec\" manually**"
fi

#run the container (or launch a new shell)
if [[ $NEW_CONTAINER == 1 ]]; then
    docker run -u haluser -i -t -v $MOUNT_DIR -d --network=host \
    --name $CONTAINER_NAME $IMAGE_NAME /bin/bash
else
    docker exec -u haluser -i -t $CONTAINER_NAME /bin/bash
fi
