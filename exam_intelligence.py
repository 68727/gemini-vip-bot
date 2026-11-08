"""
exam_intelligence.py - Comprehensive Exam Intelligence & Pattern Engine
Specialized for Indian Board Exams (CBSE, ISC) and Competitive Exams (JEE Main & JEE Advanced).
Includes real syllabus weightages, question paper blueprints, high-frequency topics, and mock paper generators.
"""

EXAM_PATTERNS = {
    "cbse_12": {
        "name": "CBSE Class 12 Board Exam",
        "marking": "70 Marks Theory (Physics/Chem) | 80 Marks (Maths)",
        "duration": "3 Hours",
        "structure": {
            "section_a": "16 Questions × 1 Mark = 16 Marks (12 MCQs + 4 Assertion-Reasoning)",
            "section_b": "5 Questions × 2 Marks = 10 Marks (Short Answer Type I)",
            "section_c": "7 Questions × 3 Marks = 21 Marks (Short Answer Type II - Derivations & Numericals)",
            "section_d": "2 Case-Based Questions × 4 Marks = 8 Marks (Paragraph with 1M, 1M, 2M sub-parts)",
            "section_e": "3 Long Answer Questions × 5 Marks = 15 Marks (Full internal choices, Derivation + 2M Numerical)",
        },
        "competency_ratio": "50% Competency-based (Case studies, Assertion-Reason, Application)",
        "physics_weightage": {
            "Optics (Ray & Wave)": "14 Marks (Highest Weightage)",
            "Electrostatics & Current Electricity": "16 Marks (Guaranteed 5M Derivation + Numericals)",
            "Magnetic Effects, Magnetism & EMI/AC": "17 Marks (Transformer / AC Generator / Biot-Savart)",
            "Modern Physics (Dual Nature, Atoms, Nuclei)": "12 Marks (High Scoring, Photoelectric effect)",
            "Semiconductor Electronics": "7 Marks (p-n junction, Rectifier, Band theory)",
        },
        "top_predicted_derivations_physics": [
            "Wavefront refraction & reflection using Huygens principle (Wave Optics - 3M/5M)",
            "Lens Maker's Formula and refraction at spherical surface (Ray Optics - 5M)",
            "Electric field due to an electric dipole on axial and equatorial lines using Gauss Law (3M/5M)",
            "Magnetic field on the axis of a circular current loop using Biot-Savart Law (3M)",
            "Impedance, resonance, and phase relationship in a series LCR AC circuit with phasor diagram (5M)",
            "Einstein's Photoelectric Equation and graphical verification of threshold frequency and stopping potential (3M)",
            "Working principle of Half-wave and Full-wave Rectifier with circuit and input/output waveforms (3M/5M)",
        ],
    },
    "isc_12": {
        "name": "ISC Class 12 (CISCE) Board Exam",
        "marking": "70 Marks Theory | 30 Marks Practical",
        "duration": "3 Hours",
        "structure": {
            "section_a": "14 Marks (14 sub-parts of 1 Mark: MCQs + Very Short Answers)",
            "section_b": "14 Marks (7 Questions of 2 Marks each)",
            "section_c": "21 Marks (7 Questions of 3 Marks each)",
            "section_d": "21 Marks (3 Questions of 5 Marks each with internal choice)",
        },
        "hallmark_rules": [
            "Strict step marking with ½ mark deductions for missing SI units or incorrect significant figures.",
            "Textbook-accurate definitions matching Council syllabus wording.",
            "Clear diagrammatic representation with labeled arrow directions for rays/currents.",
        ],
    },
    "jee_main": {
        "name": "JEE Main (NTA Pattern)",
        "marking": "300 Marks Total | 100 Marks per Subject | 3 Hours",
        "per_subject_structure": {
            "section_a": "20 Single Correct MCQs (+4 for correct, -1 for incorrect, 0 for unattempted)",
            "section_b": "10 Numerical Answer Type Questions (Attempt any 5 out of 10, +4, -1, rounded to nearest integer)",
        },
        "physics_weightage_clusters": {
            "Modern Physics & Nuclear": "12-16 Marks (3-4 Questions, High yield, Formula direct)",
            "Electrodynamics (Capacitors, Current, EMI, AC)": "24-28 Marks (6-7 Questions, Circuit mastery)",
            "Heat, Thermodynamics & KTG": "12 Marks (3 Questions, Carnot, P-V cycles, Mean free path)",
            "Mechanics (Rotation, Gravitation, SHM, Fluids)": "24-28 Marks (6-7 Questions, Multi-concept)",
            "Optics & Wave Motion": "12-16 Marks (3-4 Questions, YDSE, Doppler, Thin lenses)",
            "Semiconductors, Logic Gates & Communication": "8-12 Marks (2-3 Questions, Free scoring marks)",
        },
        "trap_patterns": [
            "Non-SI units in given problem (e.g. grams vs kg, cm vs m, μF vs F, degrees vs radians).",
            "Sign convention in optics and gravitation potential energy.",
            "Integer-type rounding: check whether answer requires nearest integer or integer magnitude.",
            "Root-mean-square (V_rms) vs peak voltage (V₀) in AC circuits.",
        ],
    },
    "jee_advanced": {
        "name": "JEE Advanced (IIT Pattern)",
        "marking": "Paper 1 & Paper 2 (180 mins each, variable marking scheme)",
        "question_types": [
            "One or more than one correct (+4 full, +3/+2/+1 partial, -2 negative)",
            "Non-negative numerical value type (decimal or integer, 0 negative)",
            "Matrix Match / List Match (+3 for complete match, -1 negative)",
            "Paragraph / Comprehension based multi-concept questions",
        ],
        "hallmark": "Multi-topic fusion (e.g. Electrostatic forces coupled with SHM or Rotational mechanics with Magnetic Lorentz force).",
    },
}

def generate_prediction_prompt(subject: str, exam: str, chapter: str | None = None) -> str:
    """Builds a structured prompt for question paper and topic trend prediction."""
    exam_key = exam.lower().strip()
    subj = subject.strip().capitalize()
    ch_info = f"specifically focusing on '{chapter}'" if chapter else "across the complete syllabus"

    return f"""
Act as the Chief Exam Paper Analyst and Senior Examiner for Indian Board and National Competitive Exams.
Provide a high-accuracy, authoritative EXAM PAPER PREDICTION REPORT for:
• Exam: {exam.upper()}
• Subject: {subj}
• Scope: {ch_info}

CRITICAL RULES:
1. STRICT TELEGRAM HTML FORMAT: Use only <b>, <i>, <code>, and <pre>. Escape &lt;, &gt;, and &amp;. Zero LaTeX ($).
2. UNICODE EQUATIONS: Use ², ₁, √, π, θ, Δ, etc.
3. GROUNDED IN 10-YEAR HISTORICAL PYQ TRENDS (2015-2026).

Provide the output following this exact structure:

🎯 <b>EXAM BLUEPRINT & PREDICTED WEIGHTAGE ({exam.upper()} - {subj})</b>
• State the overall paper section architecture and total marks.
• List top 4 highest-yield units and their predicted mark allocation.

🔥 <b>TOP 5 HIGH-PROBABILITY PREDICTED QUESTIONS / DERIVATIONS</b>
For each of the 5 predictions:
1. <b>[Topic & Predicted Section]</b>: State the exact question or derivation statement.
2. <b>Expected Marks & Question Type</b> (e.g., 3-Mark Derivation, 4-Mark Case Study, or Section B Numerical).
3. <b>Core Formula & Concept</b> in <code>tags</code>.
4. <b>Why It Is Predicted</b> (e.g., recurring 3-year gap, syllabus revision priority).

⚠️ <b>CRITICAL EXAM TRAPS & SILLY MISTAKES TO AVOID</b>
• 3 specific conceptual, sign, or calculation traps examiners set in this paper.

⚡ <b>48-HOUR HIGH-RETURN REVISION CHECKLIST</b>
• 5 key formulas/theorems to memorize tonight for guaranteed marks.
"""

def generate_mock_test_prompt(subject: str, exam: str, duration_mins: int = 30) -> str:
    """Builds a structured prompt for instant sprint mock test generation with answer guidelines."""
    subj = subject.strip().capitalize()
    ex = exam.upper().strip()

    return f"""
Act as an elite Senior Test Paper Designer for {ex}.
Generate a realistic, timed SPRINT MOCK TEST PAPER for {subj} designed to be completed in {duration_mins} minutes.

CRITICAL RULES:
1. STRICT TELEGRAM HTML: Use only <b>, <i>, <code>, and <pre>. Escape &lt;, &gt;, and &amp;. Zero LaTeX ($).
2. UNICODE MATH: Clean Unicode math symbols (², √, π, etc.).
3. ACCURATE MARKING SCHEME matching {ex} official criteria.

Structure the mock test exactly as follows:

📝 <b>{ex} SPRINT MOCK TEST: {subj.upper()}</b>
⏱ <b>Time Allowed:</b> {duration_mins} Minutes | 📊 <b>Total Marks:</b> 25 Marks

<b>SECTION A (Objective & Conceptual - 5 Marks)</b>
• Question 1 (1 Mark) [MCQ or Assertion-Reason with 4 options]
• Question 2 (1 Mark) [MCQ or Concept check]
• Question 3 (1 Mark) [Assertion-Reason with clear assertion & reason]
• Question 4 (1 Mark) [Direct 1-line definition/formula application]
• Question 5 (1 Mark) [MCQ / Trap check]

<b>SECTION B (Short Answer & Core Working - 8 Marks)</b>
• Question 6 (2 Marks) [Step-by-step conceptual problem / SI unit conversion]
• Question 7 (3 Marks) [Standard high-frequency derivation or proof]
• Question 8 (3 Marks) [Numerical problem with real values]

<b>SECTION C (Comprehensive / High-Stakes - 12 Marks)</b>
• Question 9 (4 Marks Case-Based OR 4-Mark JEE Multi-step Numerical)
• Question 10 (5 Marks or 4-Mark Advanced) [Major Derivation with coupled Numerical part]

---
<b>SOLUTIONS & STEP-WISE MARKING SCHEME</b>
(Provide concise, high-speed answer keys and step-by-step marking rubrics for each question so the student can self-evaluate immediately).
"""

