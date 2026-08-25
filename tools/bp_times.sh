#!/bin/bash
# The device runs in UTC. He is in Utah = America/Denver (MDT, UTC-6 in August).
echo "device now : $(date -u '+%Y-%m-%d %H:%M %Z')  =  $(TZ=America/Denver date '+%Y-%m-%d %I:%M %p %Z')"
echo
printf "%-10s %-18s %s\n" "route" "UTC" "LOCAL (Utah)"
for r in 000003a7 000003b5 000003b7 000003b8 000003b9 000003ba 000003bb 000003bc 000003bd; do
  d=$(ls -d /data/media/0/realdata/${r}--*--0 2>/dev/null | head -1)
  if [ -n "$d" ]; then
    u=$(stat -c %Y "$d")
    printf "%-10s %-18s %s\n" "$r" "$(date -u -d @${u} '+%m-%d %H:%M')" "$(TZ=America/Denver date -d @${u} '+%a %m-%d %I:%M %p')"
  fi
done
echo
for k in FordSynthesizeApimGps SpeedLimitPolicy StockAccStopOverride; do
  f=/data/params/d/$k
  if [ -f "$f" ]; then
    u=$(stat -c %Y "$f")
    printf "%-24s %-18s %s\n" "$k" "$(date -u -d @${u} '+%m-%d %H:%M')" "$(TZ=America/Denver date -d @${u} '+%a %m-%d %I:%M %p')"
  fi
done
