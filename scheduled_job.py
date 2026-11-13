"""
클라우드 정기 자동 실행 워커 (Scheduled Job v3.0)
- 일 6회, 회당 1작품씩 자동 실행
- 1작품 발굴(3.5-flash-lite) -> 곧바로 정통무협 에피소드 치환(3.5-flash) -> Firebase 클라우드 DB 및 로컬에 자동 누적!
- 실패 시 지수 백오프 재시도 및 모델 자동 전환(폴백) 메커니즘 탑재
"""

import os
import json
import time
import random
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

KST = timezone(timedelta(hours=9))

from pipeline import StoryHunterPipeline, DISCOVERY_MODEL, ADAPTATION_MODEL, WUXIA_STRICT_SYSTEM_INSTRUCTION

FIREBASE_DATABASE_URL = "https://kids-location-check-default-rtdb.firebaseio.com"

THEME_POOL = [
    "상업적 성공은 못했으나 스토리가 경이로운 숨은 하이스트/케이퍼 걸작",
    "서부극 또는 개척 시대 배경의 치열한 복수극 및 인간 군상 명작",
    "폐쇄 공간(밀실/고립된 지역)에서 벌어지는 심리 스릴러와 반전 추리극",
    "거대한 음모와 정보전, 배신이 얽힌 냉전 스파이물 및 첩보 스릴러",
    "독특한 규칙이나 제약 조건이 걸려 있는 배틀로얄 또는 서바이벌 게임 스토리",
    "비정한 지하 범죄 조직과 공권력의 결탁을 다룬 하드보일드 누아르",
    "시간 루프 또는 기억 상실을 활용한 기발한 미스터리 서사",
    "상인/상단 간의 피 튀기는 경제 암투와 상권 쟁탈전을 다룬 시대극",
    "세계 각국의 신화/전설을 현대적으로 재해석한 기괴하고 매력적인 스토리",
    "레딧(Reddit) 미스터리 스레드나 컬트 만화/애니메이션 중 플롯이 파격적인 작품",
    "법정 공방과 탐정의 치열한 심리 추리를 다룬 명품 법정 드라마",
    "절체절명의 탈옥 또는 난공불락 요새 침투를 다룬 서스펜스 탈출극"
]

def push_to_firebase(report: dict):
    """Firebase Realtime Database에 변환 리포트를 직접 저장 및 최대 120개 보관 큐 관리"""
    try:
        report_id = report.get("id")
        url = f"{FIREBASE_DATABASE_URL}/story_hunter/archive/{report_id}.json"
        data = json.dumps(report, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="PUT", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as res:
            if res.status in (200, 204):
                print(f">> [Firebase 클라우드 DB 동기화 완료]: {report_id}")

        # 최대 120개 초과 시 가장 오래된 항목 정리 (채택 안 된 항목 우선 정리)
        archive_url = f"{FIREBASE_DATABASE_URL}/story_hunter/archive.json"
        with urllib.request.urlopen(archive_url, timeout=15) as res:
            all_data = json.loads(res.read().decode("utf-8")) or {}
        
        if len(all_data) > 120:
            print(f">> 현재 보관량 {len(all_data)}개 (상한 120개 초과 감지). 오래된 비채택 리포트를 정리합니다...")
            # 타임스탬프 순 정렬
            sorted_items = sorted(all_data.items(), key=lambda x: x[1].get("timestamp", 0))
            excess_count = len(all_data) - 120
            deleted = 0
            for item_id, item_val in sorted_items:
                if not item_val.get("isAdopted", False):  # 채택된 중요 리포트는 보존
                    del_url = f"{FIREBASE_DATABASE_URL}/story_hunter/archive/{item_id}.json"
                    del_req = urllib.request.Request(del_url, method="DELETE")
                    with urllib.request.urlopen(del_req, timeout=10):
                        deleted += 1
                    if deleted >= excess_count:
                        break
            print(f">> 총 {deleted}개의 이전 보관 항목이 정리되었습니다.")
    except Exception as e:
        print(f"!! [Firebase 동기화/큐 정리 예외 (로컬 저장은 완료)]: {e}")

def run_single_auto_pipeline():
    print("=" * 65)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 정기 자동 발굴 & 무협 치환 워커 시작 (1작품 완결형)")
    print("=" * 65)

    pipeline = StoryHunterPipeline(storage_dir="storage")
    selected_theme = random.choice(THEME_POOL)
    print(f">> 이번 회차 테마: [{selected_theme}]")

    # 1. 1개 작품 발굴 (실패 시 재시도)
    target_work = None
    for attempt in range(3):
        try:
            works = pipeline.discover_works(theme_or_genre=selected_theme, count=1)
            if works:
                target_work = works[0]
                break
        except Exception as e:
            print(f"!! 발굴 시도 {attempt + 1} 실패: {e}")
            time.sleep(5 * (attempt + 1))

    if not target_work:
        print("!! 이번 회차 작품 발굴에 실패하여 종료합니다.")
        return

    work_title = target_work.get("title", "")
    print(f">> [발굴 완료]: {work_title} ({target_work.get('year', '')}) - {target_work.get('media', '')}")
    print(f">> 줄거리: {target_work.get('summary', '')[:70]}...")

    # 2. 에피소드 서사 요약 분석 (실패 시 모델 다운그레이드 폴백)
    analysis = None
    for attempt, model_to_use in enumerate([ADAPTATION_MODEL, DISCOVERY_MODEL]):
        try:
            print(f">> 에피소드 분석 진행 중 (모델: {model_to_use}, 시도: {attempt + 1})...")
            # 임시 모델 지정
            pipeline_text = pipeline._generate_with_retry(
                prompt=f"""
작품 [{work_title}]에서 웹소설에 차용할 만한 가장 흥미진진하고 독창적인 개별 사건/에피소드들을 추출하여 분석하십시오.
- 사건의 발단, 갈등, 반전, 해결을 딱딱하게 파편화하지 마십시오!
- 흥미진진한 스토리 요약본을 읽듯이 에피소드의 시작부터 긴장감 넘치는 전개, 기막힌 반전과 결말까지 술술 읽히는 서사형 스토리로 소개하십시오.
- Fact vs Inference 같은 분석 꼬리표는 절대 쓰지 마십시오.
참고 줄거리: {target_work.get('summary', '')}

반드시 아래 JSON 형식으로 반환하십시오:
{{
  "work_title": "{work_title}",
  "overall_premise": "작품 전체의 배경 및 핵심 전제 (2~3줄)",
  "episodes": [
    {{
      "episode_title": "에피소드 소제목",
      "story_summary": "발단부터 갈등 심화, 반전, 해결까지 한 편의 완성된 이야기처럼 자연스럽게 풀어낸 스토리 요약본 (2~4단락)",
      "key_attraction": "이 에피소드의 핵심 재미/몰입 포인트"
    }}
  ]
}}
""",
                model=model_to_use,
                temperature=0.7
            )
            analysis = pipeline._parse_json_from_response(pipeline_text)
            if analysis:
                break
        except Exception as e:
            print(f"!! 분석 에러 ({model_to_use}): {e}")
            time.sleep(7)

    if not analysis:
        print("!! 에피소드 분석 실패로 종료합니다.")
        return

    print(f">> 에피소드 {len(analysis.get('episodes', []))}개 서사 분석 완료.")

    # 3. 정통무협 치환 아이디어 도출 (仙俠 배제, 실패 시 폴백)
    wuxia_data = None
    for attempt, model_to_use in enumerate([ADAPTATION_MODEL, DISCOVERY_MODEL]):
        try:
            print(f">> 정통무협 사건단위 치환 중 (모델: {model_to_use}, 시도: {attempt + 1})...")
            wuxia_text = pipeline._generate_with_retry(
                prompt=f"""
다음 분석된 원작의 에피소드들을 [정통무협(正統武俠)] 웹소설 장르로 치환/변환하십시오.
[원작 에피소드 분석]:
{json.dumps(analysis, ensure_ascii=False, indent=2)}

[핵심 지침]:
1. 전체 작품 단위로 세계관이나 등장인물 명단을 통째로 치환하지 마십시오!
2. 오직 [각 에피소드/사건 단위]로 정통무협 치환 작업을 진행하십시오.
3. 자세한 캐릭터 이름, 설정을 길게 늘어놓지 말고, 해당 사건을 정통무협에서 어떻게 기막히게 써먹을 수 있는지 [실전 치환 아이디어들]을 다양한 각도에서 제시하십시오. (예: "마교를 쫓는 정파로 치환", "마교에 잠입한 정파 첩자를 쫓는 마교로 치환" 등)
4. 각 에피소드별 치환 아이디어는 서로 이야기가 이어지지 않아도 상관없습니다.
5. [仙俠(선협) 절대 금지]: 도술, 선술, 영약 신선화, 강시, 환혼술, 기문진법 결계 등 선협 판타지는 100% 배제하고, 순수 정통무협(구파일방, 세가, 표국, 관부, 사파, 기경팔맥, 암기, 독공, 점혈, 협과 은원)의 문법만을 사용하십시오.

반드시 아래 JSON 형식으로 반환하십시오:
{{
  "work_title": "{work_title}",
  "adapted_episodes": [
    {{
      "original_episode_title": "원작 에피소드명",
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
  ]
}}
""",
                system_instruction=WUXIA_STRICT_SYSTEM_INSTRUCTION,
                model=model_to_use,
                temperature=0.8
            )
            wuxia_data = pipeline._parse_json_from_response(wuxia_text)
            if wuxia_data:
                break
        except Exception as e:
            print(f"!! 무협 치환 에러 ({model_to_use}): {e}")
            time.sleep(7)

    if not wuxia_data:
        print("!! 무협 치환 실패로 종료합니다.")
        return

    # 4. 로컬 Markdown 저장
    filepath = pipeline.export_to_markdown(target_work, analysis, wuxia_data)
    with open(filepath, "r", encoding="utf-8") as f:
        md_content = f.read()

    # 5. Firebase 클라우드 DB에 직접 저장 (Netlify 대시보드 3번/4번 탭에 실시간 등장!)
    report_id = f"rep_{int(time.time())}_{random.randint(100, 999)}"
    kst_now = datetime.now(KST)
    report_data = {
        "id": report_id,
        "title": work_title,
        "date": kst_now.strftime("%Y-%m-%d %H:%M:%S"),
        "markdown": md_content,
        "isAdopted": False,
        "timestamp": int(time.time() * 1000)
    }
    push_to_firebase(report_data)

    print(f">> [성공 완료!]: {work_title}")
    print(f">> 로컬 저장 경로: {filepath.resolve()}")
    print("=" * 65)

if __name__ == "__main__":
    run_single_auto_pipeline()
