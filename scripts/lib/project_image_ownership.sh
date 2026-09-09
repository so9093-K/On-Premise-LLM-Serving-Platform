#!/usr/bin/env bash

# Local build ownership is separate from mutable runtime refs in .env. These
# labels let reset remove only images produced by this repository's build path;
# repository names cover images built before ownership labels were introduced.
PROJECT_IMAGE_LABEL_KEY="ai_model_serving.project"
PROJECT_IMAGE_LABEL_VALUE="on-premise-llm-serving-platform"
PROJECT_PLATFORM_IMAGE_REPOSITORY="ai-model-serving-platform"
PROJECT_VLLM_IMAGE_REPOSITORY="ai-model-serving-vllm-unified"
