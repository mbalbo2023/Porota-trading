from pathlib import Path

p = Path('.github/workflows/rc6-eod-ui-paper-deploy.yml')
s = p.read_text(encoding='utf-8')

old = '''          DIRTY_COUNT="$(git -C "$REPO" status --porcelain | wc -l | tr -d ' ')"
          FREE_BYTES="$(df -PB1 "$REPO" | awk 'NR==2{print $4}')"
          OBS_ID="$(sudo -n docker inspect -f '{{.Image}}' porota_production_observer)"
          IMAGE_SIZE="$(sudo -n docker image inspect "$OBS_ID" -f '{{.Size}}')"
          NEEDED_BYTES="$((IMAGE_SIZE * 2 + 1073741824))"
          echo "HOST_SHA=$ACTUAL_HOST_SHA DIRTY_COUNT=$DIRTY_COUNT FREE_BYTES=$FREE_BYTES IMAGE_SIZE=$IMAGE_SIZE NEEDED_BYTES=$NEEDED_BYTES"
          test "$ACTUAL_HOST_SHA" = "$BASE_SHA"
          test "$DIRTY_COUNT" = 0
          test "$FREE_BYTES" -ge "$NEEDED_BYTES"
'''
new = '''          TRACKED_DIRTY_COUNT="$(git -C "$REPO" status --porcelain=v1 --untracked-files=no | sed '/^$/d' | wc -l | tr -d ' ')"
          UNTRACKED_BEFORE="$(git -C "$REPO" status --porcelain=v1 --untracked-files=normal | grep '^?? ' || true)"
          UNTRACKED_COUNT_BEFORE="$(printf '%s\\n' "$UNTRACKED_BEFORE" | sed '/^$/d' | wc -l | tr -d ' ')"
          FREE_BYTES="$(df -PB1 "$REPO" | awk 'NR==2{print $4}')"
          OBS_ID="$(sudo -n docker inspect -f '{{.Image}}' porota_production_observer)"
          IMAGE_SIZE="$(sudo -n docker image inspect "$OBS_ID" -f '{{.Size}}')"
          NEEDED_BYTES="$((IMAGE_SIZE * 2 + 1073741824))"
          echo "HOST_SHA=$ACTUAL_HOST_SHA TRACKED_DIRTY_COUNT=$TRACKED_DIRTY_COUNT UNTRACKED_COUNT=$UNTRACKED_COUNT_BEFORE FREE_BYTES=$FREE_BYTES IMAGE_SIZE=$IMAGE_SIZE NEEDED_BYTES=$NEEDED_BYTES"
          test "$ACTUAL_HOST_SHA" = "$BASE_SHA"
          test "$TRACKED_DIRTY_COUNT" = 0
          test "$FREE_BYTES" -ge "$NEEDED_BYTES"
'''
if s.count(old) != 1:
    raise SystemExit(f'cleanliness guard anchor mismatch: {s.count(old)}')
s = s.replace(old, new, 1)

old2 = '''          git -C "$REPO" fetch --no-tags origin "$TARGET_BRANCH"
          test "$(git -C "$REPO" rev-parse "origin/$TARGET_BRANCH")" = "$TARGET_SHA"
'''
new2 = '''          git -C "$REPO" fetch --no-tags origin "$TARGET_BRANCH"
          test "$(git -C "$REPO" rev-parse "origin/$TARGET_BRANCH")" = "$TARGET_SHA"
          while IFS= read -r entry; do
            [ -n "$entry" ] || continue
            path="${entry#?? }"
            if git -C "$REPO" cat-file -e "$TARGET_SHA:$path" 2>/dev/null; then
              echo "UNTRACKED_COLLISION_WITH_TARGET=$path"
              exit 1
            fi
          done <<< "$UNTRACKED_BEFORE"
'''
if s.count(old2) != 1:
    raise SystemExit(f'fetch guard anchor mismatch: {s.count(old2)}')
s = s.replace(old2, new2, 1)

old3 = '''          test "$(git -C "$REPO" rev-parse HEAD)" = "$TARGET_SHA"
          test -z "$(git -C "$REPO" status --porcelain)"
          echo "HOST_SHA_FINAL=$TARGET_SHA HEALTH_OK=$HEALTH_OK"
'''
new3 = '''          test "$(git -C "$REPO" rev-parse HEAD)" = "$TARGET_SHA"
          test -z "$(git -C "$REPO" status --porcelain=v1 --untracked-files=no)"
          UNTRACKED_AFTER="$(git -C "$REPO" status --porcelain=v1 --untracked-files=normal | grep '^?? ' || true)"
          test "$UNTRACKED_AFTER" = "$UNTRACKED_BEFORE"
          echo "UNTRACKED_HOST_ARTIFACTS_PRESERVED=YES COUNT=$UNTRACKED_COUNT_BEFORE"
          echo "HOST_SHA_FINAL=$TARGET_SHA HEALTH_OK=$HEALTH_OK"
'''
if s.count(old3) != 1:
    raise SystemExit(f'postflight cleanliness anchor mismatch: {s.count(old3)}')
s = s.replace(old3, new3, 1)

p.write_text(s, encoding='utf-8')
print('EOD_DEPLOY_GUARD_PATCH=APPLIED')
