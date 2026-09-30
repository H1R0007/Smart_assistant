# tests/run_knowledge_tests.py

"""
Запуск всех тестов базы знаний.

python tests/run_knowledge_tests.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_dir = os.path.dirname(os.path.abspath(__file__))

    # Собираем тесты
    for test_file in [
        "test_knowledge_storage",
        "test_knowledge_embeddings",
        "test_knowledge_search",
        "test_knowledge_tracker",
    ]:
        try:
            tests = loader.loadTestsFromName(test_file)
            suite.addTests(tests)
            print(f"  ✓ {test_file}")
        except Exception as e:
            print(f"  ✗ {test_file}: {e}")

    print(f"\nЗапуск {suite.countTestCases()} тестов...\n")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Итог
    print("\n" + "=" * 60)
    if result.wasSuccessful():
        print("✅ Все тесты пройдены!")
    else:
        print(f"❌ Ошибок: {len(result.failures)}, "
              f"Исключений: {len(result.errors)}")

    sys.exit(0 if result.wasSuccessful() else 1)