// netlify/functions/hunter.js
// Netlify Serverless Function for Story Hunter

const WUXIA_STRICT_SYSTEM_INSTRUCTION = `
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
`;

async function callGemini(apiKey, prompt, systemInstruction = null, temperature = 0.7) {
  const model = process.env.GEMINI_MODEL || "gemini-3.5-flash-lite";
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`;

  const body = {
    contents: [{ parts: [{ text: prompt }] }],
    generationConfig: { temperature: temperature }
  };

  if (systemInstruction) {
    body.systemInstruction = {
      parts: [{ text: systemInstruction }]
    };
  }

  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`Gemini API Error (${res.status}): ${errText}`);
  }

  const data = await res.json();
  const text = data.candidates?.[0]?.content?.parts?.[0]?.text || "";
  return text;
}

function parseJSON(text) {
  let cleaned = text.trim();
  if (cleaned.includes("```json")) {
    cleaned = cleaned.split("```json")[1].split("```")[0].trim();
  } else if (cleaned.includes("```")) {
    cleaned = cleaned.split("```")[1].split("```")[0].trim();
  }
  return JSON.parse(cleaned);
}

exports.handler = async (event, context) => {
  if (event.httpMethod === "OPTIONS") {
    return {
      statusCode: 200,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS"
      },
      body: ""
    };
  }

  const apiKey = process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY;
  if (!apiKey) {
    return {
      statusCode: 500,
      body: JSON.stringify({ error: "Netlify 환경변수에 GEMINI_API_KEY가 설정되지 않았습니다." })
    };
  }

  try {
    const body = event.body ? JSON.parse(event.body) : {};
    const { action } = body;

    // 1. 작품 발굴 (Discover)
    if (action === "discover") {
      const theme = body.theme || "";
      const count = body.count || 3;

      let prompt = `
웹소설 에피소드 소스로 활용하기에 뛰어난 다양한 장르와 매체의 작품 ${count}개를 발굴하고 추천해주세요.
- 시대를 초월한 고전 명작, 상업적 성공은 못했으나 스토리가 뛰어난 숨은 명작, 평론가 호평작, 기발한 사건 전개의 실험작 등을 발굴하십시오.
- 매체 제한 없음: 영화, 드라마, 게임 스토리, 만화, 애니, 추리/스릴러 소설, 연극 등.
`;
      if (theme) {
        prompt += `\n- 요청 테마/장르/키워드: ${theme}\n`;
      }
      prompt += `
반드시 아래 JSON 형식의 배열로만 반환하십시오:
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
`;
      const rawText = await callGemini(apiKey, prompt, null, 0.8);
      const works = parseJSON(rawText);

      return {
        statusCode: 200,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
        body: JSON.stringify({ success: true, works: Array.isArray(works) ? works : [works] })
      };
    }

    // 2. 에피소드 분석 및 정통무협 치환 (Analyze & Adapt)
    if (action === "adapt") {
      const work = body.work;
      if (!work || !work.title) {
        return { statusCode: 400, body: JSON.stringify({ error: "작품 정보가 필요합니다." }) };
      }

      // 1단계: 에피소드 스토리 요약 분석
      const analysisPrompt = `
작품 [${work.title}]에서 웹소설에 차용할 만한 가장 흥미진진하고 독창적인 개별 사건/에피소드들을 추출하여 분석하십시오.
- 사건의 발단, 갈등, 반전, 해결을 딱딱하게 파편화하지 마십시오!
- 흥미진진한 스토리 요약본을 읽듯이 에피소드의 시작부터 긴장감 넘치는 전개, 기막힌 반전과 결말까지 술술 읽히는 서사형 스토리로 소개하십시오.
- Fact vs Inference 같은 분석 꼬리표는 절대 쓰지 마십시오.
참고 줄거리: ${work.summary || ""}

반드시 아래 JSON 형식으로 반환하십시오:
{
  "work_title": "${work.title}",
  "overall_premise": "작품 전체의 배경 및 핵심 전제 (2~3줄)",
  "episodes": [
    {
      "episode_title": "에피소드 소제목",
      "story_summary": "발단부터 갈등 심화, 반전, 해결까지 한 편의 완성된 이야기처럼 자연스럽게 풀어낸 스토리 요약본 (2~4단락)",
      "key_attraction": "이 에피소드의 핵심 재미/몰입 포인트"
    }
  ]
}
`;
      const analysisRaw = await callGemini(apiKey, analysisPrompt, null, 0.7);
      const analysis = parseJSON(analysisRaw);

      // 2단계: 정통무협 치환 아이디어 도출
      const adaptPrompt = `
다음 분석된 원작의 에피소드들을 [정통무협(正統武俠)] 웹소설 장르로 치환/변환하십시오.
[원작 에피소드 분석]:
${JSON.stringify(analysis, null, 2)}

[핵심 지침]:
1. 전체 작품 단위로 세계관이나 등장인물 명단을 통째로 치환하지 마십시오!
2. 오직 [각 에피소드/사건 단위]로 정통무협 치환 작업을 진행하십시오.
3. 자세한 캐릭터 이름, 설정을 길게 늘어놓지 말고, 해당 사건을 정통무협에서 어떻게 기막히게 써먹을 수 있는지 [실전 치환 아이디어들]을 다양한 각도에서 제시하십시오. (예: "마교를 쫓는 정파로 치환", "마교에 잠입한 정파 첩자를 쫓는 마교로 치환" 등)
4. 각 에피소드별 치환 아이디어는 서로 이야기가 이어지지 않아도 상관없습니다.
5. [仙俠(선협) 절대 금지]: 도술, 선술, 영약 신선화, 강시, 환혼술, 기문진법 결계 등 선협 판타지는 100% 배제하고, 순수 정통무협(구파일방, 세가, 표국, 관부, 사파, 기경팔맥, 암기, 독공, 점혈, 협과 은원)의 문법만을 사용하십시오.

반드시 아래 JSON 형식으로 반환하십시오:
{
  "work_title": "${work.title}",
  "adapted_episodes": [
    {
      "original_episode_title": "원작 에피소드명",
      "wuxia_situation_concept": "무협으로 변환할 핵심 상황 컨셉 (1~2줄)",
      "adaptation_ideas": [
        {
          "direction_name": "치환 방향",
          "idea_description": "구체적인 치환 아이디어 설명",
          "novel_writing_hook": "웹소설에 실제 집필할 때 독자의 반응을 이끌어낼 핵심 사이다/클라이맥스 포인트"
        }
      ],
      "orthodox_martial_elements": "이 에피소드에 어울리는 정통무협 기믹"
    }
  ]
}
`;
      const adaptRaw = await callGemini(apiKey, adaptPrompt, WUXIA_STRICT_SYSTEM_INSTRUCTION, 0.8);
      const wuxiaData = parseJSON(adaptRaw);

      // Markdown 리포트 생성
      let md = `# [정통무협 에피소드 발굴 리포트] ${work.title}\n`;
      md += `- **생성 일시**: ${new Date().toLocaleString("ko-KR")}\n`;
      md += `- **원작 정보**: ${work.title} (${work.year || ""}, ${work.country || ""}) | ${work.media || ""} (${work.genre || ""})\n\n---\n\n`;
      md += `## 1. 원작 및 추천 배경\n**전체 줄거리 요약**:\n${work.summary || ""}\n\n`;
      md += `**추천 이유**:\n${work.recommendation_reason || ""}\n\n---\n\n`;
      md += `## 2. 핵심 에피소드 분석 및 정통무협 치환 아이디어\n`;
      md += `**작품 핵심 전제**: ${analysis.overall_premise || ""}\n\n`;

      const eps = analysis.episodes || [];
      const adaptedEps = wuxiaData.adapted_episodes || [];

      eps.forEach((ep, i) => {
        md += `### ❖ [사건 ${i + 1}] ${ep.episode_title}\n`;
        md += `**📖 원작 에피소드 스토리**:\n${ep.story_summary}\n\n`;
        md += `**✨ 핵심 재미 포인트**: ${ep.key_attraction}\n\n`;

        const match = adaptedEps[i];
        if (match) {
          md += `#### ⚔️ 정통무협 치환 아이디어 (선협 배제)\n`;
          md += `**상황 컨셉**: ${match.wuxia_situation_concept || ""}\n\n`;
          (match.adaptation_ideas || []).forEach((idea, j) => {
            md += `**[방향 ${j + 1}] ${idea.direction_name}**\n`;
            md += `- **치환 아이디어**: ${idea.idea_description}\n`;
            md += `- **사이다/클라이맥스 훅**: ${idea.novel_writing_hook}\n\n`;
          });
          if (match.orthodox_martial_elements) {
            md += `**💡 추천 무협 기믹/요소**: ${match.orthodox_martial_elements}\n\n`;
          }
        }
        md += `---\n\n`;
      });

      return {
        statusCode: 200,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
        body: JSON.stringify({
          success: true,
          work: work,
          analysis: analysis,
          wuxia: wuxiaData,
          markdown: md
        })
      };
    }

    return { statusCode: 400, body: JSON.stringify({ error: "알 수 없는 action입니다." }) };
  } catch (err) {
    return {
      statusCode: 500,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
      body: JSON.stringify({ error: err.message })
    };
  }
};
