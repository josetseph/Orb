export interface KnowledgeNode {
  node_id: string;
  name: string;
  node_type: string;
  description: string;
  isolated_contexts?: string[];
  community_id?: string;
  x: number;
  y: number;
  z: number;
}
