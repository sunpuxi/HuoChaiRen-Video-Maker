#!/bin/sh
set -eu

project_dir="$(CDPATH= cd -- "$(dirname -- "$0")/../projects/clothing" && pwd)"
mkdir -p "$project_dir/audio"

say -v Tingting -r 215 -o "$project_dir/audio/scene_01.aiff" \
  '你今天穿了什么？我们每天都会穿衣服，却很少认真想过原因。'
say -v Tingting -r 215 -o "$project_dir/audio/scene_02.aiff" \
  '也许，衣服从一开始，就不只是为了保暖。'
say -v Tingting -r 215 -o "$project_dir/audio/scene_03.aiff" \
  '它还是一种语言。在你开口之前，衣服已经替你表达。'
say -v Tingting -r 215 -o "$project_dir/audio/scene_04.aiff" \
  '颜色、款式和细节，已经告诉别人你的身份、群体、态度和角色。'
say -v Tingting -r 215 -o "$project_dir/audio/scene_05.aiff" \
  '所以你选择的，不只是衣服，而是你想如何被世界看见。'

printf 'Generated demo narration in %s\n' "$project_dir/audio"
