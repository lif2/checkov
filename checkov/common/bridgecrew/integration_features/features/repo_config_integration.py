import logging
from dataclasses import dataclass
from typing import Optional, Dict

from checkov.common.bridgecrew.integration_features.base_integration_feature import BaseIntegrationFeature
from checkov.common.bridgecrew.platform_integration import bc_integration
from checkov.common.bridgecrew.severities import Severity, Severities, get_highest_severity_below_level, BcSeverities
from checkov.common.output.report import CheckType


@dataclass
class CodeCategoryType:
    IAC = "IAC"
    OPEN_SOURCE = "OPEN_SOURCE"
    SECRETS = "SECRETS"
    IMAGES = "IMAGES"
    SUPPLY_CHAIN = "SUPPLY_CHAIN"


CodeCategoryMapping = {
    CheckType.BITBUCKET_PIPELINES: CodeCategoryType.SUPPLY_CHAIN,
    CheckType.ARM: CodeCategoryType.IAC,
    CheckType.BICEP: CodeCategoryType.IAC,
    CheckType.CLOUDFORMATION: CodeCategoryType.IAC,
    CheckType.DOCKERFILE: CodeCategoryType.IAC,
    CheckType.GITHUB_CONFIGURATION: CodeCategoryType.SUPPLY_CHAIN,
    CheckType.GITHUB_ACTIONS: CodeCategoryType.SUPPLY_CHAIN,
    CheckType.GITLAB_CONFIGURATION: CodeCategoryType.SUPPLY_CHAIN,
    CheckType.GITLAB_CI: CodeCategoryType.SUPPLY_CHAIN,
    CheckType.BITBUCKET_CONFIGURATION: '',
    CheckType.HELM: CodeCategoryType.IAC,
    CheckType.JSON: CodeCategoryType.IAC,
    CheckType.YAML: CodeCategoryType.IAC,
    CheckType.KUBERNETES: CodeCategoryType.IAC,
    CheckType.KUSTOMIZE: CodeCategoryType.IAC,
    CheckType.OPENAPI: CodeCategoryType.IAC,
    CheckType.SCA_PACKAGE: CodeCategoryType.OPEN_SOURCE,
    CheckType.SCA_IMAGE: CodeCategoryType.IMAGES,
    CheckType.SECRETS: CodeCategoryType.SECRETS,
    CheckType.SERVERLESS: CodeCategoryType.IAC,
    CheckType.TERRAFORM: CodeCategoryType.IAC,
    CheckType.TERRAFORM_PLAN: CodeCategoryType.IAC
}


class CodeCategoryConfiguration:
    def __init__(self, category: str, soft_fail_threshold: Severity, hard_fail_threshold: Severity):
        self.category = category
        self.soft_fail_threshold = soft_fail_threshold
        self.hard_fail_threshold = hard_fail_threshold

    def is_global_soft_fail(self) -> bool:
        return self.hard_fail_threshold == Severities[BcSeverities.OFF]

    def get_skip_check_threshold(self) -> Severity:
        severity = get_highest_severity_below_level(self.soft_fail_threshold.level)
        return severity or Severities[BcSeverities.NONE]


class RepoConfigIntegration(BaseIntegrationFeature):
    def __init__(self, bc_integration):
        super().__init__(bc_integration, order=0)
        self.skip_paths = set()
        self.enforcement_rule = None
        self.code_category_configs: Dict[str, CodeCategoryConfiguration] = {}

    def is_valid(self) -> bool:
        return (
            self.bc_integration.is_integration_configured()
            and not self.bc_integration.skip_download
            and not self.integration_feature_failures
        )

    def pre_scan(self) -> None:
        try:
            if not self.bc_integration.customer_run_config_response:
                logging.debug('In the pre-scan for repo config settings, but nothing was fetched from the platform')
                self.integration_feature_failures = True
                return

            # It is possible that they will have two different and conflicting rules for this repo - one for the VCS
            # integration that matches the value of --repo-id (org/repo), and one for the CLI upload repo (e.g., customer_org/repo).
            # For the skip paths, we can just combine the lists and call it good. For enforcement rules, we will
            # prioritize the VCS integration over CLI, and warn them that the rules should match for these repos.

            self._set_exclusion_paths(self.bc_integration.customer_run_config_response['vcsConfig'])
            self._set_enforcement_rules(self.bc_integration.customer_run_config_response['enforcementRules'])

        except Exception:
            self.integration_feature_failures = True
            logging.debug("Scanning without applying scanning configs from the platform.", exc_info=True)

    @staticmethod
    def _get_code_category_object(code_category_config, code_category_type: str) -> Optional[CodeCategoryConfiguration]:
        if code_category_type not in code_category_config:
            return None
        soft_fail_threshold = Severities[code_category_config[code_category_type]['softFailThreshold']]
        hard_fail_threshold = Severities[code_category_config[code_category_type]['hardFailThreshold']]
        return CodeCategoryConfiguration(code_category_type, soft_fail_threshold, hard_fail_threshold)

    def _set_exclusion_paths(self, vcs_config) -> None:
        for section in vcs_config['scannedFiles']['sections']:
            repos = section['repos']
            if any(repo for repo in repos if self.bc_integration.repo_matches(repo)):
                logging.debug(f'Found path exclusion config section for repo: {section}')
                self.skip_paths.update(section['rule']['excludePaths'])

        logging.debug(f'Skipping the following paths based on platform settings: {self.skip_paths}')

    def _set_enforcement_rules(self, enforcement_rules_config) -> None:
        rules = enforcement_rules_config['rules']
        default_rule = next(r for r in rules if r['mainRule'] is True)
        other_rules = [r for r in rules if r != default_rule]

        matched_rules = []

        for rule in other_rules:
            if any(repo for repo in rule['repositories'] if self.bc_integration.repo_matches(repo['accountName'])):
                matched_rules.append(rule)

        if len(matched_rules) > 1:
            logging.warning(f'Found {len(matched_rules)} enforcement rules for the specified repo. This likely means '
                            f'that one rule was created for the VCS repo, and another rule for the CLI repo. You '
                            f'should update the configurations in the platform to ensure that the following repos '
                            f'are all in the same rule group:')
            exact_match_rule = None
            for rule in matched_rules:
                for repo in rule['repositories']:
                    repo_name = repo['accountName']
                    if self.bc_integration.repo_matches(repo_name):
                        logging.warning(f'- {repo_name}')
                        if repo_name == self.bc_integration.repo_id:
                            if exact_match_rule:
                                logging.debug('Found multiple rules that exactly match --repo-id - likely the same '
                                              'name across multiple VCSes. Using the first one.')
                            else:
                                exact_match_rule = rule

            if not exact_match_rule:
                logging.debug('Did not find any rules with a repo name that exactly matched --repo-id; taking the '
                              'first one.')

            self.enforcement_rule = exact_match_rule or matched_rules[0]
        elif len(matched_rules) == 0:
            logging.info('Did not find any enforcement rules for the specified repo; using the default rule')
            self.enforcement_rule = default_rule
        else:
            logging.info('Found exactly one matching enforcement rule for the specified repo')
            self.enforcement_rule = matched_rules[0]

        logging.debug(f'Selected the following enforcement rule (it will not be applied unless --use-platform-enforcement-rules is specified):')
        logging.debug(self.enforcement_rule)

        for code_category_type in [value for attr, value in CodeCategoryType.__dict__.items() if not attr.startswith("__")]:
            config = RepoConfigIntegration._get_code_category_object(self.enforcement_rule['codeCategories'], code_category_type)
            if config:
                self.code_category_configs[code_category_type] = config

    @staticmethod
    def _convert_raw_check(policy):
        metadata = {
            'id': policy['id'],
            'name': policy['title'],
            'category': policy['category'],
            'scope': {
                'provider': policy['provider']
            }
        }
        check = {
            'metadata': metadata,
            'definition': policy['conditionQuery']
        }
        return check


integration = RepoConfigIntegration(bc_integration)
