"""
웹소설 에피소드 발굴 및 정통무협장르 치환/변환 파이프라인 (v2.0)
- 수정요청 3 반영: 에피소드 분석 파편화 해소 (스토리 요약본 형태, Fact vs Inference 제거)
- 수정요청 4 반영: 에피소드/사건 단위 독립 치환, 전체 세계관/인물 매핑 제거, 실전 무협 치환 아이디어 제시 중심
"""

import os
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from google import genai
from google.genai import types

DISCOVERY_MODEL = "gemini-3.5-flash-lite"
ADAPTATION_MODEL = "gemini-4-hydrogen"
FALLBACK_ADAPTATION_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]

WUXIA_STRICT_SYSTEM_INSTRUCTION = """
당신은 정통무협(正統武俠) 웹소설 집필을 위한 에피소드 기획 수석 작가입니다.

[절대 규칙 - 仙俠(선협) 및 도교 판타지 전면 금지]:
1. 선협, 신선, 도술, 선술, 법보, 영약 환단 복용을 통한 우화등선/불로불사, 부적, 강시(시체 조종), 환혼술(영혼 이동/빙의/환생), 기문진법 결계, 요괴 등 도교 판타지 요소를 100% 엄격히 배제하십시오.
2. 오직 [정통무협(전통 무협)] 세계관의 문법만을 적용하십시오:
   - 세력: 구파일방, 오대세가, 사파연맹/흑도, 녹림칠십이채, 장강수로채, 표국, 살수문파, 관부(포청천, 금의위, 순검, 동창).
   - 수단: 내공 심법(기경팔맥, 삼초, 주천, 점혈), 정통 권각수/검법/도법, 암기, 당문 독공, 변장술/역용술.
   - 갈등: 강호의 의(俠), 은원(恩怨), 혈연과 복수, 문파의 체면과 이권 다툼, 비급 쟁탈, 배신과 충성, 인간 군상의 심리전.
3. [에피소드 단위 독립 치환 원칙]:
   - 작품 전체를 하나의 이야기로 엮으려 하지 마십시오.
   - 오직 '해당 에피소드/사건' 자체를 정통무협 장르로 어떻게 기막히게 치환할 수 있는지, 작가가 바로 집필에 영감을 얻을 수 있는 다채로운 [치환/변환 아이디어]를 구체적인 상황과 함께 제시하십시오.
   - 각 에피소드의 치환 아이디어들은 서로 이어지지 않아도 무방합니다.
"""


class StoryHunterPipeline:
    def __init__(self, api_key: Optional[str] = None, storage_dir: str = "storage"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
        
        self.client = genai.Client(api_key=self.api_key)
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.storage_dir / "discovered_history.json"
        self._init_history()

    def _init_history(self):
        if not self.history_file.exists():
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)

    def get_history(self) -> List[str]:
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def add_to_history(self, title: str):
        history = self.get_history()
        if title not in history:
            history.append(title)
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)

    def _generate_with_retry(self, prompt: str, system_instruction: Optional[str] = None, temperature: float = 0.7, model: str = ADAPTATION_MODEL) -> str:
        """
        API 오류 및 할당량 제한에 유연하게 대처하는 스마트 재시도 & 다계층 폴백 함수:
        1순위: Gemini 4 Hydrogen (초고품질 서사 치환)
        2순위: Gemini 3.8 Flash (최신 고속 모델)
        3순위: Gemini 3.5 Flash (안정적 백업)
        4순위: Gemini 3.5 Flash Lite (최종 안전망)
        """
        models_to_try = [model]
        if model == ADAPTATION_MODEL:
            for fallback in FALLBACK_ADAPTATION_MODELS:
                if fallback not in models_to_try:
                    models_to_try.append(fallback)
        elif model != DISCOVERY_MODEL and DISCOVERY_MODEL not in models_to_try:
            models_to_try.append(DISCOVERY_MODEL)

        last_error = None
        for current_model in models_to_try:
            print(f">> [호출 시도] 모델: {current_model}")
            max_attempts = 4
            for attempt in range(max_attempts):
                try:
                    config_args = {"temperature": temperature}
                    if system_instruction:
                        config_args["system_instruction"] = system_instruction
                    
                    response = self.client.models.generate_content(
                        model=current_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(**config_args)
                    )
                    return response.text
                except Exception as e:
                    last_error = e
                    err_msg = str(e).lower()
                    print(f"!! [API 경고 - 시도 {attempt + 1}/{max_attempts}] 모델({current_model}) 호출 실패: {e}")

                    # 1. 429 분당 요청량 제한 (Rate limit / Quota)
                    if "429" in err_msg or "resource_exhausted" in err_msg or "quota" in err_msg:
                        if "daily" in err_msg or "day" in err_msg or "limit" in err_msg:
                            print(f">> 일일 할당량 관련 제한 감지됨. 다음 모델로 전환합니다.")
                            break  # 다음 폴백 모델로 즉시 전환
                        wait_sec = 60
                        print(f">> 분당 호출 제한(429) 감지: {wait_sec}초간 쿨다운 대기 후 재시도합니다...")
                        time.sleep(wait_sec)
                        continue

                    # 2. 503 서버 혼잡, 504 타임아웃, 502/500 일시적 장애
                    if any(code in err_msg for code in ["503", "504", "502", "500", "overloaded", "unavailable", "timeout", "busy"]):
                        wait_sec = (attempt + 1) * 15
                        print(f">> 구글 API 서버 혼잡/타임아웃: {wait_sec}초 대기 후 재시도합니다...")
                        time.sleep(wait_sec)
                        continue

                    # 그 외 일반 에러도 잠시 대기 후 재시도
                    if attempt < max_attempts - 1:
                        time.sleep(5 * (attempt + 1))

        # 모든 모델 및 재시도 실패 시 예외 발생
        raise RuntimeError(f"모든 모델 호출 및 재시도 실패. 마지막 에러: {last_error}")

    # 1. 작품 발굴 및 추천
    def discover_works(self, theme_or_genre: Optional[str] = None, count: int = 3) -> List[Dict[str, Any]]:
        history = self.get_history()
        history_str = ", ".join(history[-30:]) if history else "없음"

        prompt = f"""
웹소설 에피소드 소스로 활용하기에 뛰어난 다양한 장르와 매체의 작품 {count}개를 발굴하고 추천해주세요.

[발굴 원칙]:
- 최신 유행작이나 유명작에 편중되지 마십시오.
- 시대를 초월한 고전 명작, 상업적 성공은 못했으나 스토리가 뛰어난 숨은 명작, 평론가/수상작, 커뮤니티(레딧 등)에서 스토리로 회자되는 작품, 기발한 사건 전개를 가진 실험작 등을 폭넓게 발굴하십시오.
- 매체 제한 없음: 영화, 미드/해외드라마, 콘솔/PC 게임 스토리, 만화, 애니메이션, 해외 추리/스릴러 소설, 연극/희곡, 미국 코믹스 등.
- 기존에 이미 발굴한 작품 목록은 제외하십시오: [{history_str}]
"""
        if theme_or_genre:
            prompt += f"\n- 요청 테마/장르/키워드: {theme_or_genre}\n"

        prompt += """
결과는 반드시 아래 JSON 형식의 배열로만 반환하십시오:
[
  {
    "title": "작품명 (원제 포함)",
    "year": "발표 연도",
    "country": "제작 국가",
    "media": "매체 (영화/게임/소설/드라마 등)",
    "genre": "원작 장르",
    "summary": "핵심 줄거리 및 독창적 설정",
    "recommendation_reason": "스토리/에피소드적 가치 및 추천 이유"
  }
]
"""
        text = self._generate_with_retry(prompt, temperature=0.8, model=DISCOVERY_MODEL)
        data = self._parse_json_from_response(text)
        return data if isinstance(data, list) else [data]

    # 2. 작품 및 에피소드 분석 (수정요청 3 반영: 스토리 요약본 형태, Fact vs Inference 제거)
    def analyze_episodes(self, work_title: str, additional_info: Optional[str] = None) -> Dict[str, Any]:
        prompt = f"""
작품 [{work_title}]에서 웹소설에 차용할 만한 가장 흥미진진하고 독창적인 개별 사건/에피소드들을 추출하여 분석하십시오.

[작성 지침 - 매우 중요]:
1. 사건의 발단, 갈등 관계, 반전, 해결 메커니즘을 항목별로 딱딱하게 쪼개지(파편화하지) 마십시오!
2. 위 요소들을 자연스럽게 녹여내어, 마치 '흥미진진한 스토리 요약본'을 읽듯이 에피소드의 시작부터 긴장감 넘치는 전개, 기막힌 반전과 결말까지 술술 읽히는 서사형 스토리로 소개하십시오.
3. Fact vs Inference 같은 분석 꼬리표는 절대 쓰지 마십시오.
4. 웹소설 작가가 읽고 "이 사건 구조 정말 재밌다! 내 소설에 써먹어야겠다"라고 느낄 수 있도록 사건의 재미와 서스펜스 포인트를 생생하게 담아내십시오.
"""
        if additional_info:
            prompt += f"\n참고 정보: {additional_info}\n"

        prompt += """
결과는 반드시 아래 JSON 형식으로 반환하십시오:
{
  "work_title": "작품명",
  "overall_premise": "작품 전체의 배경 및 핵심 전제 (2~3줄 요약)",
  "episodes": [
    {
      "episode_title": "에피소드/사건 소제목 (예: 고립된 설산에서의 독살 사건과 죄수 호송대의 반란)",
      "story_summary": "발단부터 갈등의 심화, 의외의 트릭이나 반전, 해결까지 한 편의 완성된 이야기처럼 자연스럽고 몰입감 있게 풀어낸 스토리 요약본 (2~4단락 분량)",
      "key_attraction": "이 에피소드가 독자를 몰입시키는 핵심 재미 포인트 (예: 밀실 심리전, 쫓고 쫓기는 속도감, 예상 밖의 배신 등)"
    }
  ]
}
"""
        text = self._generate_with_retry(prompt, temperature=0.7)
        return self._parse_json_from_response(text)

    # 3. 정통무협 에피소드 단위 치환/변환 (수정요청 4 반영: 에피소드별 독립 치환, 인물/세계관 통짜 변환 제거, 치환 아이디어 제시 중심)
    def adapt_to_wuxia(self, episode_analysis: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""
다음 분석된 원작의 에피소드들을 [정통무협(正統武俠)] 웹소설 장르로 치환/변환하십시오.

[원작 에피소드 분석 데이터]:
{json.dumps(episode_analysis, ensure_ascii=False, indent=2)}

[핵심 지침 - 반드시 준수]:
1. 전체 작품 단위로 세계관이나 등장인물 명단, 세력 관계를 통째로 치환하지 마십시오!
2. 오직 [각 에피소드/사건 단위]로 정통무협 치환 작업을 진행하십시오.
3. 자세한 캐릭터 이름, 구체적인 설정 등을 소설 집필하듯 길게 늘어놓지 마십시오.
4. 해당 에피소드의 매력적인 상황(예: 기막힌 추격전, 인질 맞교환 트릭, 밀실에서의 배신과 심리전 등)을 정통무협 장르에서 어떻게 기막히게 써먹을 수 있는지 [실전 치환 아이디어들]을 다양한 각도에서 제시하십시오!
   - 예: 추격전 에피소드라면:
     * 아이디어 1: "마교의 비밀 결사조를 추격하는 정파 명문세가 고수들의 암야 추격전으로 치환"
     * 아이디어 2: "마교 내부에 잠입한 정파 첩자가 비밀 장부를 탈취해 달아나고, 마교 십대추살조가 맹렬히 뒤쫓는 상황으로 치환"
     * 아이디어 3: "표국의 배신자를 쫓는 표사들과 그 뒤를 덮치는 녹림 수채의 3파전 매복 추격전으로 치환"
5. 각 에피소드별 치환 아이디어는 서로 이야기가 이어지지 않아도 상관없습니다. 각각 독립된 훌륭한 무협 에피소드 소재로 활용할 수 있어야 합니다.
6. [仙俠(선협) 절대 금지]: 도술, 선술, 영약 신선화, 강시, 환혼술, 기문진법 결계 등 선협 판타지는 100% 배제하고, 순수 정통무협(구파일방, 세가, 표국, 관부, 사파, 기경팔맥, 암기, 독공, 점혈, 협과 은원)의 문법만을 사용하십시오.

결과는 반드시 아래 JSON 형식으로 반환하십시오:
{{
  "work_title": "원작명",
  "adapted_episodes": [
    {{
      "original_episode_title": "원작 에피소드명",
      "wuxia_situation_concept": "무협으로 변환할 핵심 상황 컨셉 (1~2줄)",
      "adaptation_ideas": [
        {{
          "direction_name": "치환 방향 (예: 정파 vs 마교 첩보 추격전)",
          "idea_description": "어떤 세력과 인물들이 어떤 위기와 갈등 속에 놓이며, 사건이 어떻게 전개되고 반전이 일어나는지에 대한 구체적 치환 아이디어 설명",
          "novel_writing_hook": "웹소설에 실제 집필할 때 독자의 반응을 이끌어낼 핵심 사이다/클라이맥스 포인트"
        }}
      ],
      "orthodox_martial_elements": "이 에피소드에 어울리는 정통무협 기믹 (예: 특정 경공술, 사천당문 암기/독, 기경팔맥 점혈 트릭, 관부 금의위와의 묵계 등)"
    }}
  ]
}}
"""
        text = self._generate_with_retry(
            prompt,
            system_instruction=WUXIA_STRICT_SYSTEM_INSTRUCTION,
            temperature=0.8
        )
        return self._parse_json_from_response(text)

    # 4. 결과 Markdown 저장 (가독성 높은 기획 리포트 형식)
    def export_to_markdown(
        self,
        work_info: Dict[str, Any],
        analysis: Dict[str, Any],
        wuxia_data: Dict[str, Any]
    ) -> Path:
        safe_title = re.sub(r'[\\/*?:"<>|]', "", work_info.get("title", "work")).strip().replace(" ", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{safe_title}.md"
        filepath = self.storage_dir / filename

        lines = []
        lines.append(f"# [정통무협 에피소드 발굴 리포트] {work_info.get('title')}")
        lines.append(f"- **생성 일시**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"- **원작 정보**: {work_info.get('title')} ({work_info.get('year', '')}, {work_info.get('country', '')}) | {work_info.get('media', '')} ({work_info.get('genre', '')})")
        lines.append("\n---\n")

        lines.append("## 1. 원작 및 추천 배경")
        lines.append(f"**전체 줄거리 요약**:\n{work_info.get('summary', '')}\n")
        lines.append(f"**추천 이유**:\n{work_info.get('recommendation_reason', '')}\n")
        lines.append("\n---\n")

        lines.append("## 2. 핵심 에피소드 분석 및 정통무협 치환 아이디어")
        lines.append(f"**작품 핵심 전제**: {analysis.get('overall_premise', '')}\n")
        
        episodes_analysis = analysis.get("episodes", [])
        adapted_episodes = wuxia_data.get("adapted_episodes", [])

        for i, ep in enumerate(episodes_analysis, 1):
            ep_title = ep.get("episode_title", f"사건 {i}")
            lines.append(f"### ❖ [사건 {i}] {ep_title}")
            lines.append(f"**📖 원작 에피소드 스토리**:\n{ep.get('story_summary', '')}\n")
            lines.append(f"**✨ 핵심 재미 포인트**: {ep.get('key_attraction', '')}\n")

            matching_adapted = None
            if i - 1 < len(adapted_episodes):
                matching_adapted = adapted_episodes[i - 1]

            if matching_adapted:
                lines.append(f"#### ⚔️ 정통무협 치환 아이디어 (선협 배제)")
                lines.append(f"**상황 컨셉**: {matching_adapted.get('wuxia_situation_concept', '')}\n")
                
                ideas = matching_adapted.get("adaptation_ideas", [])
                for j, idea in enumerate(ideas, 1):
                    lines.append(f"**[방향 {j}] {idea.get('direction_name', '')}**")
                    lines.append(f"- **치환 아이디어**: {idea.get('idea_description', '')}")
                    lines.append(f"- **사이다/클라이맥스 훅**: {idea.get('novel_writing_hook', '')}\n")
                
                if matching_adapted.get("orthodox_martial_elements"):
                    lines.append(f"**💡 추천 무협 기믹/요소**: {matching_adapted.get('orthodox_martial_elements')}\n")

            lines.append("\n---\n")

        content = "\n".join(lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        self.add_to_history(work_info.get("title", ""))
        return filepath

    def _parse_json_from_response(self, text: str) -> Any:
        try:
            cleaned = text.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            return json.loads(cleaned)
        except Exception as e:
            match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"AI 응답에서 JSON 파싱 실패: {e}\n{text[:300]}")
