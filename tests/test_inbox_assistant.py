import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inbox_assistant import (  # noqa: E402
    Email, categorize, extract_deadlines, needs_reply, score_email, sender_name, triage,
)


class TestInboxAssistant(unittest.TestCase):
    def test_interview_email_is_urgent_career(self):
        e = Email("recruiter@co.com", "Interview Invitation", "Schedule an interview next week.")
        result = triage([e])[0]
        self.assertEqual(result.priority, "URGENT")
        self.assertEqual(result.category, "Career")

    def test_promotion_scores_negative(self):
        e = Email("newsletter@shop.com", "BIG SALE 50% OFF!!", "Limited time. Unsubscribe here.")
        score, _ = score_email(e)
        self.assertLess(score, 0)
        self.assertEqual(categorize(e), "Promotion")

    def test_subject_keywords_weigh_more_than_body(self):
        in_subject = Email("a@b.com", "Deadline", "hello")
        in_body = Email("a@b.com", "hello", "deadline")
        self.assertGreater(score_email(in_subject)[0], score_email(in_body)[0])

    def test_edu_sender_boost(self):
        edu = Email("prof@school.edu", "hi", "hi")
        other = Email("prof@school.com", "hi", "hi")
        self.assertGreater(score_email(edu)[0], score_email(other)[0])

    def test_deadline_extraction(self):
        text = "Submit by Friday midnight, or at the latest before Nov 12. Forms due 11/15."
        self.assertEqual(extract_deadlines(text), ["Friday midnight", "Nov 12", "11/15"])

    def test_results_sorted_by_score(self):
        emails = [
            Email("shop@x.com", "sale", "promo"),
            Email("prof@u.edu", "Urgent exam deadline", "due tomorrow"),
        ]
        results = triage(emails)
        self.assertEqual(results[0].sender, "prof@u.edu")

    def test_needs_reply_for_direct_question(self):
        e = Email("recruiter@co.com", "Interview", "Please reply with your availability.")
        self.assertTrue(needs_reply(e, "Career"))

    def test_no_reply_for_promotions_or_noreply(self):
        promo = Email("deals@shop.com", "Want 50% off?", "sale")
        bot = Email("noreply@bank.com", "Can you verify?", "statement ready")
        self.assertFalse(needs_reply(promo, "Promotion"))
        self.assertFalse(needs_reply(bot, "Normal"))

    def test_draft_mentions_sender_and_deadline(self):
        e = Email("jane.doe@co.com", "Interview Invitation", "Please reply by Nov 12.")
        result = triage([e])[0]
        self.assertIn("Hi Jane Doe", result.draft)
        self.assertIn("Nov 12", result.draft)

    def test_sender_name(self):
        self.assertEqual(sender_name("professor.lee@university.edu"), "Professor Lee")


if __name__ == "__main__":
    unittest.main()
