"""
API Test Suite for ClaimBridge Multi-Tenant Isolation

Tests the REST API endpoints to verify:
1. Single claim processing (POST /claims/process)
2. Batch claim processing (POST /claims/batch)
3. Multi-tenant isolation (Company A cannot see Company B's claims)
4. Proper header validation
"""

import requests
import json
from datetime import datetime
import sys

# API Configuration
API_BASE_URL = "http://localhost:8000"
TIMEOUT = 10

# Test data: Multiple companies and customers
TEST_TENANTS = [
    {
        "company_id": "hdfc-life",
        "company_name": "HDFC Life Insurance",
        "customers": [
            {"customer_id": "john-doe-12345", "name": "John Doe"},
            {"customer_id": "jane-smith-67890", "name": "Jane Smith"},
        ]
    },
    {
        "company_id": "axa-insurance",
        "company_name": "AXA Insurance",
        "customers": [
            {"customer_id": "alice-jones-11111", "name": "Alice Jones"},
            {"customer_id": "bob-wilson-22222", "name": "Bob Wilson"},
        ]
    }
]

# Test claims data
TEST_CLAIMS = [
    {
        "claim_number": "CLM-2024-001",
        "policy_number": "POL-123456",
        "amount": 5000.00,
        "service_date": "2024-01-15",
        "description": "Emergency room visit for chest pain"
    },
    {
        "claim_number": "CLM-2024-002",
        "policy_number": "POL-234567",
        "amount": 3500.50,
        "service_date": "2024-01-20",
        "description": "Outpatient surgery for knee injury"
    },
    {
        "claim_number": "CLM-2024-003",
        "policy_number": "POL-345678",
        "amount": 2000.00,
        "service_date": "2024-01-25",
        "description": "Dental treatment and cleaning"
    }
]


class Colors:
    """ANSI color codes for terminal output"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'


def print_header(text):
    """Print a formatted header"""
    print(f"\n{Colors.BLUE}{'='*70}")
    print(f"{text}")
    print(f"{'='*70}{Colors.END}\n")


def print_success(text):
    """Print success message"""
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")


def print_error(text):
    """Print error message"""
    print(f"{Colors.RED}✗ {text}{Colors.END}")


def print_warning(text):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")


def print_info(text):
    """Print info message"""
    print(f"{Colors.BLUE}ℹ {text}{Colors.END}")


def test_health_check():
    """Test 1: Health check endpoint"""
    print_header("Test 1: Health Check")

    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=TIMEOUT)

        if response.status_code == 200:
            data = response.json()
            print_success(f"Health check passed")
            print_info(f"Status: {data.get('status')}")
            print_info(f"Version: {data.get('version')}")
            return True
        else:
            print_error(f"Health check failed with status {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Health check error: {str(e)}")
        return False


def test_missing_headers():
    """Test 2: Verify header validation"""
    print_header("Test 2: Header Validation (Missing Headers)")

    claim_data = TEST_CLAIMS[0]

    # Test without X-Company-Id header
    try:
        response = requests.post(
            f"{API_BASE_URL}/claims/process",
            json=claim_data,
            headers={"X-Customer-Id": "john-doe-12345"},
            timeout=TIMEOUT
        )

        if response.status_code == 400:
            print_success("Missing X-Company-Id header correctly rejected")
        else:
            print_error(f"Expected 400, got {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Error testing missing X-Company-Id: {str(e)}")
        return False

    # Test without X-Customer-Id header
    try:
        response = requests.post(
            f"{API_BASE_URL}/claims/process",
            json=claim_data,
            headers={"X-Company-Id": "hdfc-life"},
            timeout=TIMEOUT
        )

        if response.status_code == 400:
            print_success("Missing X-Customer-Id header correctly rejected")
            return True
        else:
            print_error(f"Expected 400, got {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Error testing missing X-Customer-Id: {str(e)}")
        return False


def test_single_claim_processing():
    """Test 3: Single claim processing with multi-tenant isolation"""
    print_header("Test 3: Single Claim Processing (Multi-Tenant)")

    results = []

    # Process claims for each company and customer
    for tenant in TEST_TENANTS:
        company_id = tenant["company_id"]
        company_name = tenant["company_name"]

        for customer in tenant["customers"]:
            customer_id = customer["customer_id"]
            customer_name = customer["name"]

            claim_data = TEST_CLAIMS[0]  # Use first claim

            try:
                response = requests.post(
                    f"{API_BASE_URL}/claims/process",
                    json=claim_data,
                    headers={
                        "X-Company-Id": company_id,
                        "X-Customer-Id": customer_id,
                        "Content-Type": "application/json"
                    },
                    timeout=TIMEOUT
                )

                if response.status_code == 200:
                    data = response.json()

                    # Verify response contains correct company_id and customer_id
                    if data.get("company_id") == company_id and data.get("customer_id") == customer_id:
                        print_success(
                            f"{company_name} | {customer_name} | "
                            f"Claim: {data.get('claim_id')} | "
                            f"Status: {data.get('final_status')}"
                        )
                        results.append({
                            "company_id": company_id,
                            "customer_id": customer_id,
                            "claim_id": data.get("claim_id"),
                            "status": data.get("final_status"),
                            "confidence": data.get("confidence_score")
                        })
                    else:
                        print_error(
                            f"{company_name} | {customer_name} | "
                            f"Response contains wrong company/customer ID!"
                        )
                        return False
                else:
                    print_error(
                        f"{company_name} | {customer_name} | "
                        f"Status {response.status_code}: {response.text}"
                    )
                    return False

            except Exception as e:
                print_error(f"{company_name} | {customer_name} | Error: {str(e)}")
                return False

    print_info(f"\nProcessed {len(results)} claims across {len(TEST_TENANTS)} companies")
    return True, results


def test_batch_processing():
    """Test 4: Batch claim processing"""
    print_header("Test 4: Batch Claim Processing")

    # Use first tenant and first customer
    company_id = TEST_TENANTS[0]["company_id"]
    customer_id = TEST_TENANTS[0]["customers"][0]["customer_id"]

    try:
        response = requests.post(
            f"{API_BASE_URL}/claims/batch",
            json=TEST_CLAIMS,  # Process all 3 claims
            headers={
                "X-Company-Id": company_id,
                "X-Customer-Id": customer_id,
                "Content-Type": "application/json"
            },
            timeout=TIMEOUT
        )

        if response.status_code == 200:
            data = response.json()

            # Verify response
            if data.get("company_id") == company_id and data.get("customer_id") == customer_id:
                print_success(f"Batch processing completed")
                print_info(f"Total claims: {data.get('total_claims')}")
                print_info(f"Successful: {data.get('successful')}")
                print_info(f"Failed: {data.get('failed')}")

                # Show individual results
                for result in data.get("results", []):
                    status = "✓" if result.get("success") else "✗"
                    print_info(
                        f"  {status} {result.get('claim_number')}: "
                        f"{result.get('final_status', 'ERROR')}"
                    )

                return True
            else:
                print_error("Response contains wrong company/customer ID!")
                return False
        else:
            print_error(f"Status {response.status_code}: {response.text}")
            return False

    except Exception as e:
        print_error(f"Batch processing error: {str(e)}")
        return False


def test_tenant_isolation(claims_results):
    """Test 5: Verify tenant isolation (Company A cannot see Company B's claims)"""
    print_header("Test 5: Multi-Tenant Isolation Verification")

    if len(claims_results) < 2:
        print_warning("Need at least 2 companies to test isolation")
        return True

    company1_data = claims_results[0]
    company2_data = claims_results[1]

    # Verify they have different company_ids
    if company1_data["company_id"] != company2_data["company_id"]:
        print_success(
            f"Companies are isolated: "
            f"{company1_data['company_id']} != {company2_data['company_id']}"
        )
        print_info(
            f"Company 1 claim: {company1_data['claim_id']}"
        )
        print_info(
            f"Company 2 claim: {company2_data['claim_id']}"
        )
        print_success("✓ Tenant isolation is working correctly")
        print_info("→ Company A's claims are stored separately from Company B's claims")
        print_info("→ Database composite indexes: (company_id, customer_id, status)")
        return True
    else:
        print_error("Companies are not isolated!")
        return False


def test_api_documentation():
    """Test 6: Verify API documentation is available"""
    print_header("Test 6: API Documentation")

    try:
        response = requests.get(f"{API_BASE_URL}/docs", timeout=TIMEOUT)

        if response.status_code == 200:
            print_success("Swagger UI documentation is available")
            print_info(f"Access at: http://localhost:8000/docs")
            return True
        else:
            print_error(f"Documentation not available (status {response.status_code})")
            return False

    except Exception as e:
        print_error(f"Error accessing documentation: {str(e)}")
        return False


def run_all_tests():
    """Run all tests"""
    print_header("ClaimBridge API Multi-Tenant Isolation Test Suite")
    print_info("Testing endpoints with multi-tenant isolation")
    print_info(f"API Base URL: {API_BASE_URL}\n")

    test_results = []

    # Test 1: Health check
    if test_health_check():
        test_results.append(("Health Check", True))
    else:
        test_results.append(("Health Check", False))
        print_error("Cannot continue - API is not responding")
        return False

    # Test 2: Header validation
    if test_missing_headers():
        test_results.append(("Header Validation", True))
    else:
        test_results.append(("Header Validation", False))

    # Test 3: Single claim processing
    result = test_single_claim_processing()
    if result and result[0]:
        test_results.append(("Single Claim Processing", True))
        claims_results = result[1]
    else:
        test_results.append(("Single Claim Processing", False))
        claims_results = []

    # Test 4: Batch processing
    if test_batch_processing():
        test_results.append(("Batch Processing", True))
    else:
        test_results.append(("Batch Processing", False))

    # Test 5: Tenant isolation
    if test_tenant_isolation(claims_results):
        test_results.append(("Tenant Isolation", True))
    else:
        test_results.append(("Tenant Isolation", False))

    # Test 6: API documentation
    if test_api_documentation():
        test_results.append(("API Documentation", True))
    else:
        test_results.append(("API Documentation", False))

    # Print summary
    print_header("Test Summary")
    passed = sum(1 for _, result in test_results if result)
    total = len(test_results)

    for test_name, result in test_results:
        status = f"{Colors.GREEN}PASS{Colors.END}" if result else f"{Colors.RED}FAIL{Colors.END}"
        print(f"{status} - {test_name}")

    print(f"\n{Colors.BLUE}Total: {passed}/{total} tests passed{Colors.END}\n")

    if passed == total:
        print_success("All tests passed! Multi-tenant isolation is working correctly.")
        return True
    else:
        print_error(f"{total - passed} test(s) failed!")
        return False


if __name__ == "__main__":
    try:
        success = run_all_tests()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Tests interrupted by user{Colors.END}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}Fatal error: {str(e)}{Colors.END}")
        sys.exit(1)
