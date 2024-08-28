#! /bin/bash
# Helper Script for Tagging Moonraker Releases

PRINT_ONLY="n"
KLIPPER_PATH="$HOME/klipper"
REMOTE=""
DESCRIBE="describe --always --tags --long"

# Get Tag and Klipper Path
TAG=$1
shift
while :; do
    case $1 in
        -k|--klipper-path)
            shift
            KLIPPER_PATH=$1
            ;;
        -p|--print)
            PRINT_ONLY="y"
            ;;
        *)
            break
    esac

    shift
done


if [ ! -d "$KLIPPER_PATH/.git" ];  then
    echo "Invalid Klipper Path: $KLIPPER_PATH"
fi
echo "Klipper found at $KLIPPER_PATH"
GIT_CMD="git -C $KLIPPER_PATH"

ALL_REMOTES="$( $GIT_CMD remote | tr '\n' ' ' | awk '{gsub(/^ +| +$/,"")} {print $0}' )"
echo "Found Klipper Remotes: $ALL_REMOTES"
for val in $ALL_REMOTES; do
    REMOTE_URL="$( $GIT_CMD remote get-url $val | awk '{gsub(/^ +| +$/,"")} {print tolower($0)}' )"
    match="$( echo $REMOTE_URL | grep -Ecm1 '(klipper3d|kevinoconnor)/klipper'|| true )"
    if [ "$match" -eq 1  ]; then
        echo "Found Remote $val"
        REMOTE="$val"
        break
    fi
done

[ "$REMOTE" = "" ] && echo "Unable to find a valid remote" && exit 1

$GIT_CMD fetch $REMOTE

DESC="$( $GIT_CMD $DESCRIBE $REMOTE/master | awk '{gsub(/^ +| +$/,"")} {print $0}' )"
HASH="$( $GIT_CMD rev-parse $REMOTE/master | awk '{gsub(/^ +| +$/,"")} {print $0}' )"

if [ "$PRINT_ONLY" = "y" ]; then
    echo "
Tag: $TAG
Repo: Klipper
Branch: Master
Version: $DESC
Commit: $HASH
"
else
    echo "Adding Tag $TAG"
    git tag -a $TAG -m "Moonraker Version $TAG
Klipper Tag Data
repo: klipper
branch: master
version: $DESC
commit: $HASH
"
fi
