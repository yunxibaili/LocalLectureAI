# Round 10B Real Math Audio Report

## Test Environment

* Windows
* 新东方云课堂
* 专升本高等数学
* `INSTRUMENTATION=true`
* Session: `sessions/2026-09-23_12-49-22`

## Course Content Verification

Transcript excerpts confirm this was a real 高等数学 lecture:

* “如果你极限学不好的话…整个高等数学当中最基础的一个知识点就是求极限”
* “第二个叫导数…这叫导数和微分”
* “什么叫积分…不定积分还是定积分”
* “这三章…叫一元积分学”
* “第十章叫做多元积分学…重积分…二重积分”
* “无穷极数…就是把数列转化成极限思想”

No account, user, or personal information recorded.

## Audio Timeline

| Event | Time / Offset |
|---|---|
| capture start | 12:49:22 |
| Whisper load end | 12:49:25, 2381 ms |
| recorder started | 12:49:25 |
| first audio callback | 12:49:25, ~87 ms after recorder start |
| first transcript | session_t 33.984s |
| last transcript | session_t 333.511s |
| capture end | 12:54:26 |

## Windows

* total windows: 10
* accepted: 10
* RMS drops: 0
* short drops: 0

## Transcription

* transcribed windows: 10
* zero-segment windows: 0
* transcript events: 217
* segment counts: `[21, 27, 17, 20, 16, 24, 29, 23, 22, 18]`

## Longest Gap

* start: `session_t=115.758`
* end: `session_t=120.718`
* duration: `4.96s`

## Gap Evidence

The longest gap is a normal short speech pause inside active windows:

* audio windows overlapping the period: window 2 and window 3
* accepted: yes
* RMS: well above threshold (`~0.066`)
* segments: present
* transcripts: present before and after

No 100+ second gap occurred.

## Conclusion

**未复现。**

This real 专升本高等数学 5-minute sample did not reproduce the Round 7 ~190s
transcript gap.

Observed:

* A: no callback? **No**
* B: RMS/short drop? **No**
* C: zero-segment? **No**
* D: segment but no transcript? **No**
* longest gap: **4.96s**, normal speech pause

This supports the conclusion that the Round 7 190s gap was not reproduced in
this healthy real-math sample, but it does not prove the Round 7 event was
impossible or permanently resolved.

Final `/api/ps`: `{"models":[]}`
