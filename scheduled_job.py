"""
클라우드 정기 자동 실행 워커 (Scheduled Job v3.0)
- 일 6회, 회당 1작품씩 자동 실행
- 1작품 발굴(3.5-flash-lite) -> 곧바로 정통무협 에피소드 치환(3.5-flash) -> Firebase 클라우드 DB 및 로컬에 자동 누적!
- 실패 시 지수 백오프 재시도 및 모델 자동 전환(폴백) 메커니즘 탑재
"""

import os
import sys
import json
import time
import random
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

KST = timezone(timedelta(hours=9))

from pipeline import StoryHunterPipeline, DISCOVERY_MODEL, ADAPTATION_MODEL, WUXIA_STRICT_SYSTEM_INSTRUCTION, is_duplicate_work

FIREBASE_DATABASE_URL = "https://kids-location-check-default-rtdb.firebaseio.com"

THEME_POOL = [
    "상업적 성공은 못했으나 스토리가 경이로운 숨은 하이스트/케이퍼 걸작",
    "서부극 또는 개척 시대 배경의 치열한 복수극 및 인간 군상 명작",
    "비정한 지하 범죄 조직과 공권력의 결탁을 다룬 하드보일드 누아르",
    "세계 각국의 신화/전설을 현대적으로 재해석한 기괴하고 매력적인 스토리",
    "레딧(Reddit) 미스터리 스레드나 컬트 작품 중 플롯이 파격적인 이야기",
    "법정 공방과 치열한 심리 공방을 다룬 명품 법정 드라마",
    "방대하고 치밀한 세계관과 압도적 몰입감을 자랑하는 대작 스토리 게임",
    "탄탄한 각본과 입체적 인물 군상이 돋보이는 명품 미드/영드 시리즈",
    "교묘한 트릭과 숨 막히는 두뇌 싸움이 펼쳐지는 본격 추리물",
    "원작을 뛰어넘는 기발한 재해석과 치밀한 전개를 보여주는 하이퀄리티 팬픽 및 웹연재작",
    "고뇌와 신념의 충돌, 다크한 이면이 돋보이는 입체적인 히어로물 및 안티히어로 서사",
    "마지막 순간까지 예측을 뒤엎으며 전율을 안기는 기막힌 반전 서사 명작",
    "독창적 컨셉과 기발한 내러티브 아이디어가 돋보이는 인디 게임 스토리",
    "치밀한 복선과 장대한 서사 구조를 지닌 대작 스토리 애니메이션 및 만화"
]

def _get_firebase_auth():
    """Firebase 비공개 규칙 통과용 인증 (FIREBASE_DB_SECRET 또는 FIREBASE_SERVICE_ACCOUNT)"""
    db_secret = os.environ.get("FIREBASE_DB_SECRET", "").strip()
    if db_secret:
        return f"?auth={db_secret}", {}
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if sa_json:
        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests
            info = json.loads(sa_json)
            creds = service_account.Credentials.from_service_account_info(
                info,
                scopes=[
                    "https://www.googleapis.com/auth/userinfo.email",
                    "https://www.googleapis.com/auth/firebase.database",
                ],
            )
            creds.refresh(google.auth.transport.requests.Request())
            return "", {"Authorization": f"Bearer {creds.token}"}
        except Exception as e:
            print(f"!! Firebase 서비스 계정 인증 토큰 생성 실패: {e}")
    return "", {}


def _firebase_request(path: str, method: str = "GET", payload=None, timeout: int = 15):
    query, auth_headers = _get_firebase_auth()
    url = f"{FIREBASE_DATABASE_URL}/{path.lstrip('/')}{query}"
    headers = {"Content-Type": "application/json", **auth_headers}
    data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data_bytes, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read().decode("utf-8")
        return res.status, (json.loads(body) if body else None)


def log_run_failure(stage: str, detail: str):
    """실패 사유를 Firebase(story_hunter/run_logs)에 남기고, 깃허브에 '실패(빨간 X)'로 표시되도록 종료"""
    try:
        kst_now = datetime.now(KST)
        log = {
            "time": kst_now.strftime("%Y-%m-%d %H:%M:%S"),
            "stage": stage,
            "detail": str(detail)[:1500],
            "timestamp": int(time.time() * 1000),
        }
        _firebase_request(f"story_hunter/run_logs/fail_{int(time.time())}.json", method="PUT", payload=log, timeout=15)
    except Exception as e:
        print(f"!! 실패 로그 기록조차 실패: {e}")
    print(f"!! [{stage}] 실패로 종료: {detail}")
    sys.exit(1)


def push_to_firebase(report: dict):
    """Firebase Realtime Database에 변환 리포트를 저장 (저장 실패 시 예외 발생 → 회차 실패 처리)"""
    report_id = report.get("id")
    status, _ = _firebase_request(f"story_hunter/archive/{report_id}.json", method="PUT", payload=report, timeout=20)
    if status not in (200, 204):
        raise RuntimeError(f"Firebase 저장 응답 코드 {status}")
    print(f">> [Firebase 클라우드 DB 저장 완료]: {report_id}")

    try:
        # 최대 120개 초과 시 가장 오래된 항목 정리 (채택 안 된 항목 우선 정리)
        _, all_data = _firebase_request("story_hunter/archive.json", method="GET", timeout=15)
        all_data = all_data or {}

        if len(all_data) > 120:
            print(f">> 현재 보관량 {len(all_data)}개 (상한 120개 초과 감지). 오래된 비채택 리포트를 정리합니다...")
            sorted_items = sorted(all_data.items(), key=lambda x: x[1].get("timestamp", 0))
            excess_count = len(all_data) - 120
            deleted = 0
            for item_id, item_val in sorted_items:
                if not item_val.get("isAdopted", False):  # 채택된 중요 리포트는 보존
                    _firebase_request(f"story_hunter/archive/{item_id}.json", method="DELETE", timeout=10)
                    deleted += 1
                    if deleted >= excess_count:
                        break
            print(f">> 총 {deleted}개의 이전 보관 항목이 정리되었습니다.")
    except Exception as e:
        print(f"!! [120개 보관 정리 중 예외 (저장 자체는 완료)]: {e}")


def fetch_existing_titles_from_firebase() -> list:
    """Firebase 클라우드 아카이브에 이미 존재하는 모든 작품 제목을 실시간 조회 (중복 발굴 100% 방지용)"""
    try:
        _, data = _firebase_request("story_hunter/archive.json", method="GET", timeout=15)
        data = data or {}
        return [v.get("title", "") for v in data.values() if isinstance(v, dict) and v.get("title")]
    except Exception as e:
        print(f"!! Firebase 기존 작품 목록 조회 실패 (로컬 이력으로 대체): {e}")
        return []


def run_single_auto_pipeline():
    print("=" * 65)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 정기 자동 발굴 & 무협 치환 워커 시작 (1작품 완결형)")
    print("=" * 65)

    pipeline = StoryHunterPipeline(storage_dir="storage")

    # 클라우드(Firebase) + 로컬 아카이브 전체 제목 실시간 동기화 (수동/자동 합산 중복 100% 차단)
    cloud_titles = fetch_existing_titles_from_firebase()
    all_existing_titles = list(set(pipeline.get_history() + cloud_titles))
    print(f">> [중복 방지 동기화] 클라우드+로컬 기존 작품 {len(all_existing_titles)}개 제외 목록 확보")

    # 1. 숨은 명작 발굴 및 1.5단계 경량 팩트체크 검증 (Gemini 4 Hydrogen 쿼터 낭비 100% 방지)
    target_work = None
    verification = None
    max_discovery_attempts = 5

    for attempt in range(max_discovery_attempts):
        selected_theme = random.choice(THEME_POOL)
        print(f"\n>> [발굴/검증 시도 {attempt + 1}/{max_discovery_attempts}] 테마: [{selected_theme}]")
        try:
            candidates = pipeline.discover_works(theme_or_genre=selected_theme, count=1, extra_exclude=all_existing_titles)
            if not candidates:
                continue
            candidate = candidates[0]
            cand_title = candidate.get("title", "")
            print(f">> 발굴 후보: {cand_title} ({candidate.get('media', '')})")

            # 1차 코드 레벨 중복 차단
            dup_match = is_duplicate_work(cand_title, all_existing_titles)
            if dup_match:
                print(f"!! [1단계 중복 작품 차단]: '{cand_title}' ↔ 기존 '{dup_match}' 중복 감지!")
                print(f"   -> [Gemini 4 Hydrogen 쿼터 보존(0회 소모)] 즉시 폐기하고 다른 작품 탐색")
                all_existing_titles.append(cand_title)
                continue

            # 1.5단계 경량 팩트체크 및 출처/자료 좌표 확보 (경량 모델 - 고위 모델 쿼터 소모 0회)
            print(f">> [1.5단계 팩트체크 & 서사 분석 가능여부 검증 중...] (모델: {DISCOVERY_MODEL})")
            verif = pipeline.verify_work_candidate(candidate)
            if verif.get("is_valid", False):
                canonical_cand = verif.get("canonical_title", cand_title)
                # 2차 코드 레벨 중복 차단 (공식 원제 정규화 후 재검사)
                dup_match2 = is_duplicate_work(canonical_cand, all_existing_titles)
                if dup_match2:
                    print(f"!! [1.5단계 중복 작품 차단]: '{canonical_cand}' ↔ 기존 '{dup_match2}' 중복 감지!")
                    print(f"   -> [Gemini 4 Hydrogen 쿼터 보존(0회 소모)] 즉시 폐기하고 다른 작품 탐색")
                    all_existing_titles.append(canonical_cand)
                    continue

                target_work = candidate
                verification = verif
                print(f">> [1.5단계 검증 통과! 실존 작품 및 서사 확인]: {canonical_cand}")
                print(f"   - 창작자/원작: {verif.get('creator_or_author', '확인됨')}")
                break
            else:
                reason = verif.get("rejection_reason", "실존 불명 또는 서사 자료 불충분")
                print(f"!! [1.5단계 검증 탈락]: {cand_title} - 사유: {reason}")
                print(f"   -> [Gemini 4 Hydrogen 쿼터 보존(0회 소모)] 즉시 폐기하고 다음 숨은 명작을 탐색합니다.")
        except Exception as e:
            print(f"!! 발굴/검증 시도 {attempt + 1} 중 일시 오류: {e}")
            time.sleep(10)

    if not target_work or not verification:
        log_run_failure("1단계/1.5단계 발굴 및 검증", "유효한 실존 작품 후보 확보 실패")

    canonical_title = verification.get("canonical_title") or target_work.get("title", "")
    creator = verification.get("creator_or_author", "")
    ref_sources = verification.get("reference_sources", [])
    ref_sources_str = "\n".join([f"- {s}" for s in ref_sources]) if ref_sources else "- 위키피디아, TVTropes 및 관련 커뮤니티 데이터"

    print(f"\n>> [최종 선정 작품 확정]: {canonical_title} ({target_work.get('year', '')}) | 제작: {creator}")
    print(f">> 출처 및 참고 좌표:\n{ref_sources_str}")

    # 2단계 & 3단계 통합: 에피소드 서사 심층 분석 + 정통무협 치환 (Gemini 4 Hydrogen 단 1회 호출로 쿼터 극대화!)
    print(f"\n>> 2단계: 에피소드 서사 분석 & 정통무협 치환 일체형 실행 (우선 모델: {ADAPTATION_MODEL})...")
    print(f">> [Gemini 4 Hydrogen 1회 호출로 분석과 치환을 완결하여 쿼터를 50% 절약합니다.]")
    try:
        unified_prompt = f"""
당신은 전 세계의 서사 매체(영화, 드라마, 소설, 만화, 게임 등)를 정통무협(正統武俠) 웹소설로 재창조하는 최고의 스토리 디렉터입니다.

작품 [{canonical_title}] (제작/원작자: {creator}, 매체: {target_work.get('media', '')}, 연도: {target_work.get('year', '')})을 바탕으로,
1) 원작의 핵심 사건/에피소드를 생생한 서사형 스토리로 심층 분석하고,
2) 각 에피소드를 [정통무협(正統武俠)] 장르로 완벽히 치환하십시오.

[작품 출처 및 참고 문서 좌표]:
{ref_sources_str}

[원작 기본 줄거리/설정 개요]:
{target_work.get('summary', '')}

[작성 및 치환 지침 - 절대 준수]:
1. [실제 서사 종합]: 위 작품의 공식 줄거리, 위키, TV Tropes, 레딧 스레드, 대본, 팬포럼 등에 기록된 실제 사건 전개와 갈등을 종합 활용하십시오.
2. [서사형 스토리텔링]: 에피소드를 항목별로 딱딱하게 파편화하지 말고, 발단부터 갈등 심화, 반전, 해결까지 한 편의 완성된 이야기처럼 자연스럽게 풀어낸 '스토리 요약본(2~4단락)'으로 작성하십시오. (Fact vs Inference 등 분석 꼬리표 금지)
3. [에피소드 단위 독립 치환]: 전체 세계관이나 인물 명단을 통째로 바꾸지 말고, 오직 [각 사건/에피소드 단위]로 정통무협 치환 아이디어를 다각도에서 제시하십시오.
4. [仙俠(선협) 절대 금지]: 도술, 선술, 영약 신선화, 강시, 환혼술, 기문진법 결계 등 선협 판타지는 100% 배제하고, 순수 정통무협(구파일방, 세가, 표국, 관부, 사파, 기경팔맥, 암기, 독공, 점혈, 협과 은원)의 문법만을 사용하십시오.

반드시 아래 JSON 형식으로 반환하십시오:
{{
  "work_title": "{canonical_title}",
  "overall_premise": "작품 전체의 배경 및 핵심 전제 (2~3줄)",
  "episodes": [
    {{
      "episode_title": "에피소드 소제목",
      "story_summary": "발단부터 갈등 심화, 반전, 해결까지 한 편의 완성된 이야기처럼 자연스럽게 풀어낸 스토리 요약본 (2~4단락)",
      "key_attraction": "이 에피소드의 핵심 재미/몰입 포인트",
      "wuxia_adaptation": {{
        "wuxia_situation_concept": "무협으로 변환할 핵심 상황 컨셉 (1~2줄)",
        "adaptation_ideas": [
          {{
            "direction_name": "치환 방향",
            "idea_description": "구체적인 치환 아이디어 설명",
            "novel_writing_hook": "웹소설에 실제 집필할 때 독자의 반응을 이끌어낼 핵심 사이다/클라이맥스 포인트"
          }}
        ],
        "orthodox_martial_elements": "이 에피소드에 어울리는 정통무협 기믹"
      }}
    }}
  ]
}}
"""
        response_text = pipeline._generate_with_retry(
            prompt=unified_prompt,
            system_instruction=WUXIA_STRICT_SYSTEM_INSTRUCTION,
            model=ADAPTATION_MODEL,
            temperature=0.75
        )
        combined_data = pipeline._parse_json_from_response(response_text)
    except Exception as e:
        log_run_failure("2단계 서사 분석 & 무협 치환", e)

    if not combined_data or not combined_data.get("episodes"):
        log_run_failure("2단계 서사 분석 & 무협 치환", "빈 응답 또는 에피소드 누락")

    print(f">> [성공] 에피소드 {len(combined_data.get('episodes', []))}개 서사 분석 및 정통무협 치환 완결 (단 1회 호출 성공).")

    # 4. 로컬 Markdown 저장
    filepath = pipeline.export_to_markdown(target_work, combined_data, verification=verification)
    with open(filepath, "r", encoding="utf-8") as f:
        md_content = f.read()

    # 5. Firebase 클라우드 DB에 직접 저장 (Netlify 대시보드 3번/4번 탭에 실시간 등장!)
    report_id = f"rep_{int(time.time())}_{random.randint(100, 999)}"
    kst_now = datetime.now(KST)
    report_data = {
        "id": report_id,
        "title": canonical_title,
        "date": kst_now.strftime("%Y-%m-%d %H:%M:%S"),
        "markdown": md_content,
        "isAdopted": False,
        "timestamp": int(time.time() * 1000)
    }
    try:
        push_to_firebase(report_data)
    except Exception as e:
        log_run_failure("4단계 Firebase 저장", e)

    print(f">> [성공 완료!]: {canonical_title}")
    print(f">> 로컬 저장 경로: {filepath.resolve()}")
    print("=" * 65)

if __name__ == "__main__":
    run_single_auto_pipeline()
