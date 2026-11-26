# 이슈·밈 자동 수집기

매시간 아래 소스에서 **오늘(한국시간) 올라온 글**을 본문·사진·영상까지 저장하고, 새 글 목록을 텔레그램으로 보내줍니다.

| 구분 | 소스 |
|---|---|
| 국내 커뮤니티 | 더쿠 핫게, 디시 실시간베스트, 디시 미국주식갤(개념글만), 에펨 포텐, 네이트판 톡커들의 선택, 인스티즈 이슈, 루리웹 유머베스트 |
| 트렌드 | 구글 트렌드 KR, 시그널 실시간 검색어, 네이버 뉴스 랭킹, X 트렌드(trends24), 유튜브 인기(API 키 넣으면) |
| 해외 밈 | Reddit(memes, me_irl, MadeMeSmile, aww 등), 9GAG |
| 인스타 참고 계정 | 공식 Graph API(Business Discovery) – 토큰 넣으면 동작 |

## 처음 한 번 설정
1. `install.bat` 더블클릭 → 필요한 패키지 설치 + 예약 작업(매시간 정각, 매일 23:55) 등록
2. 텔레그램 봇 만들기: 텔레그램에서 **@BotFather** → `/newbot` → 받은 토큰을 `config.json`의 `"bot_token"`에 붙여넣기
3. 만든 봇에게 아무 메시지 보내기 → `telegram_setup.bat` 실행 → 나온 숫자를 `"chat_id"`에 넣기 → 테스트 메시지 확인
4. `run_now.bat` 으로 바로 한 번 수집 (끝나면 오늘 정리 페이지가 열림)

## 결과 보는 곳
- `data\날짜\index.html` : 그날 수집한 전부(소스별, 썸네일, 원문 링크, 본문)
- `data\날짜\소스\시각_글번호_제목\` : 사진/영상 원본 + post.json
- `data\날짜\_trends\` : 시간대별 트렌드 스냅샷
- `logs\collector.log` : 실행 기록

## 자주 바꾸는 설정 (config.json)
- `sources` : 소스 켜고 끄기 (true/false)
- `max_pages` : 소스별로 몇 페이지까지 볼지
- `telegram.photo_previews_per_run` : 실행마다 텔레그램으로 보낼 사진 미리보기 개수 (0이면 안 보냄)
- `telegram.trend_hours` : 트렌드 요약을 보낼 시각
- `reddit_subs` : 해외 밈 서브레딧 목록
- `download_videos` : 영상도 받을지

## 인스타 참고 계정 (공식 API)
Meta가 허용하는 공식 방법(Business Discovery)이라 계정 위험이 없습니다. 대신 준비물이 있어요.
1. 내 인스타 계정이 **프로페셔널(비즈니스/크리에이터)** 이고 **페이스북 페이지에 연결**돼 있어야 함
2. https://developers.facebook.com 에서 앱 생성 → 권한 `instagram_basic`, `pages_show_list`, `business_management` 로 토큰 발급(Graph API Explorer) → 장기 토큰으로 교환
3. `GET /me/accounts?fields=instagram_business_account` 로 나온 id를 `ig_user_id`에, 토큰을 `access_token`에 넣기
- 상대 계정도 비즈니스/크리에이터 계정이어야 보입니다(개인 계정은 API로 못 읽음).
- 장기 토큰은 약 60일마다 갱신해야 합니다.

## 끄기
`uninstall.bat` (예약 작업만 지우고 수집물은 남김)

※ 컴퓨터가 켜져 있을 때만 수집됩니다. 꺼져 있던 시간대 글은 다음 실행 때 '오늘 글'이면 따라잡습니다.
※ 수집한 글·사진은 소재 참고용입니다. 원본 그대로 재업로드하면 인스타 원본성 정책에 걸릴 수 있으니 편집해서 쓰세요.
