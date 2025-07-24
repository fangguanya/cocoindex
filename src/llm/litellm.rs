use async_openai::Client as OpenAIClient;
use async_openai::config::OpenAIConfig;

pub use super::openai::Client;

impl Client {
    pub async fn new_litellm(address: Option<String>) -> anyhow::Result<Self> {
        let address = address.unwrap_or_else(|| "http://127.0.0.1:4000".to_string());
        // 优先使用OPENAI_API_KEY，因为大多数LiteLLM部署都兼容OpenAI API格式
        let api_key = std::env::var("OPENAI_API_KEY")
            .or_else(|_| std::env::var("LITELLM_API_KEY"))
            .ok();
        let mut config = OpenAIConfig::new().with_api_base(address);
        if let Some(api_key) = api_key {
            config = config.with_api_key(api_key);
        }
        Ok(Client::from_parts(OpenAIClient::with_config(config)))
    }
}
